/**
 * The planner, in code: OpenStreetMap finds places, code scores them, and the
 * **server's** LLM gateway writes the words.
 *
 * This mirrors the split in `phone-app/farfromhome_device/agent/` and the brief's rule
 * that *"code does searching, filtering, scoring, routing and scheduling; the LLM only
 * produces text"*:
 *
 * 1. parse the request into a spec (keywords first, so it works with no network),
 * 2. find candidates over a radius with Overpass (or the built-in set offline),
 * 3. filter out anything that is not a safe public place,
 * 4. score them in code and pick the best,
 * 5. ask `POST /api/llm/plan` on the server for the hints, title and game items,
 * 6. assemble a validated `Plan`.
 *
 * The destination is **never** sent to the LLM: the request carries only the mood, the
 * group, the duration, the distance and the category. A model that has not been told
 * the place cannot leak it, which matters because free tiers may train on prompts.
 */

import { api } from "./api";
import { bearingDeg, haversineM, interpolate, moveTowards, pathLengthM } from "./geo";
import type { Plan, PlanWaypoint, RevealRule } from "./engine";

// --------------------------------------------------------------------------- //
// the request
// --------------------------------------------------------------------------- //
export interface TripSpec {
  text: string;
  mood: "calm" | "chill" | "playful" | "adventure" | "romantic" | "foodie";
  people: number;
  transport: "walk" | "bike" | "car" | "transit";
  maxMinutes: number;
  rangeKm: [number, number];
  interests: string[];
  foodExclusions: string[];
  startLat: number;
  startLng: number;
  offline: boolean;
}

const MOOD_WORDS: Record<string, TripSpec["mood"]> = {
  calm: "calm", quiet: "calm", peaceful: "calm", slow: "calm",
  chill: "chill", relaxed: "chill", easy: "chill", lazy: "chill",
  playful: "playful", fun: "playful", silly: "playful",
  adventure: "adventure", adventurous: "adventure", explore: "adventure", wild: "adventure",
  romantic: "romantic", date: "romantic", sunset: "romantic",
  foodie: "foodie", hungry: "foodie", food: "foodie", eat: "foodie",
};

const INTEREST_WORDS: Record<string, string> = {
  coffee: "coffee", cafe: "coffee", chai: "coffee", tea: "coffee", bakery: "coffee",
  food: "food", restaurant: "food", snack: "food", lunch: "food", dinner: "food",
  nature: "nature", forest: "nature", trees: "nature", green: "nature",
  park: "park", garden: "park",
  view: "viewpoint", viewpoint: "viewpoint", lookout: "viewpoint", hill: "viewpoint",
  beach: "beach", sea: "beach", shore: "beach", coast: "beach", water: "beach", waves: "beach",
  market: "market", bazaar: "market", shopping: "market",
  temple: "temple", church: "temple", mosque: "temple", shrine: "temple",
  history: "heritage", historic: "heritage", old: "heritage", ruins: "heritage",
  museum: "heritage", art: "museum", gallery: "museum",
  bar: "bar", beer: "bar", drink: "bar",
  books: "bookshop", library: "bookshop",
};

const TRANSPORT_WORDS: Record<string, TripSpec["transport"]> = {
  walk: "walk", walking: "walk", stroll: "walk", wander: "walk", foot: "walk",
  bike: "bike", bicycle: "bike", cycle: "bike", cycling: "bike", scooter: "bike",
  car: "car", drive: "car", driving: "car", taxi: "car",
  bus: "transit", train: "transit", transit: "transit", metro: "transit", ferry: "transit",
};

const WORD_NUMBERS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8,
  ten: 10, couple: 2, pair: 2, solo: 1, alone: 1, myself: 1, "just me": 1,
};

/** Parse a sentence into a spec. Deterministic and offline; the LLM refines nothing here. */
export function parseSpec(
  text: string,
  startLat: number,
  startLng: number,
  offline = false
): TripSpec {
  const lower = ` ${text.toLowerCase()} `;

  let mood: TripSpec["mood"] = "chill";
  for (const word of Object.keys(MOOD_WORDS).sort((a, b) => b.length - a.length)) {
    if (lower.includes(` ${word}`)) {
      mood = MOOD_WORDS[word];
      break;
    }
  }

  let transport: TripSpec["transport"] = "walk";
  for (const word of Object.keys(TRANSPORT_WORDS).sort((a, b) => b.length - a.length)) {
    if (lower.includes(` ${word}`)) {
      transport = TRANSPORT_WORDS[word];
      break;
    }
  }

  const peopleMatch = lower.match(/\b(\d{1,2})\s*(?:people|persons?|of us|friends?|mates?)\b/);
  let people = peopleMatch ? Number(peopleMatch[1]) : 0;
  if (!people) {
    for (const word of Object.keys(WORD_NUMBERS).sort((a, b) => b.length - a.length)) {
      if (lower.includes(` ${word}`)) {
        people = WORD_NUMBERS[word];
        break;
      }
    }
  }
  people = Math.max(1, Math.min(12, people || 2));

  let maxMinutes = 0;
  const hourMatch = lower.match(/(\d+(?:\.\d+)?)\s*(?:hours?|hrs?)/);
  const minuteMatch = lower.match(/(\d+)\s*(?:minutes?|mins?)/);
  if (hourMatch) maxMinutes = Math.round(Number(hourMatch[1]) * 60) + (minuteMatch ? Number(minuteMatch[1]) : 0);
  else if (minuteMatch) maxMinutes = Number(minuteMatch[1]);
  else if (lower.includes("half an hour")) maxMinutes = 30;
  else if (lower.includes("an hour") || lower.includes("one hour")) maxMinutes = 60;
  else if (lower.includes("all day")) maxMinutes = 360;
  maxMinutes = Math.max(20, Math.min(360, maxMinutes || 90));

  const interests: string[] = [];
  for (const word of Object.keys(INTEREST_WORDS).sort((a, b) => b.length - a.length)) {
    if (lower.includes(word)) {
      const interest = INTEREST_WORDS[word];
      if (!interests.includes(interest)) interests.push(interest);
    }
  }
  if (!interests.length) interests.push("coffee", "viewpoint");

  const rangeMatch = lower.match(/(\d+(?:\.\d+)?)\s*(?:km|kilometres?|kilometers?)/);
  let rangeKm: [number, number];
  if (rangeMatch) {
    const value = Number(rangeMatch[1]);
    rangeKm = lower.includes("under") || lower.includes("less than")
      ? [Math.max(0.5, value * 0.4), value]
      : [Math.max(0.5, value * 0.5), value];
  } else {
    // Scale with the time budget and the mode, which is what a person would expect.
    const speed = transport === "walk" ? 3.2 : transport === "bike" ? 10 : 25;
    const high = Math.max(1.2, (maxMinutes / 60) * speed);
    rangeKm = [Math.max(0.5, high * 0.3), high];
  }

  const foodExclusions: string[] = [];
  for (const token of ["vegetarian", "vegan", "no meat", "no pork", "no beef", "no dairy", "gluten", "peanut", "no seafood"]) {
    if (lower.includes(token)) foodExclusions.push(token);
  }

  return {
    text,
    mood,
    people,
    transport,
    maxMinutes,
    rangeKm,
    interests,
    foodExclusions,
    startLat,
    startLng,
    offline,
  };
}

// --------------------------------------------------------------------------- //
// places
// --------------------------------------------------------------------------- //
export interface Candidate {
  id: string;
  name: string;
  lat: number;
  lng: number;
  category: string;
  tags: Record<string, string>;
  distanceM: number;
  score: number;
  reasons: string[];
}

/** OSM selectors per interest, the same set the handheld uses. */
const OVERPASS_FILTERS: Record<string, string> = {
  coffee: 'node["amenity"="cafe"]',
  cafe: 'node["amenity"="cafe"]',
  food: 'node["amenity"~"restaurant|fast_food"]',
  nature: 'node["leisure"~"park|garden|nature_reserve"]',
  park: 'node["leisure"~"park|garden"]',
  viewpoint: 'node["tourism"="viewpoint"]',
  beach: 'node["natural"="beach"]',
  market: 'node["amenity"="marketplace"]',
  temple: 'node["amenity"="place_of_worship"]',
  heritage: 'node["historic"]',
  museum: 'node["tourism"="museum"]',
  bar: 'node["amenity"="bar"]',
  bookshop: 'node["shop"="books"]',
};

const PUBLIC_CATEGORIES = new Set(["beach", "park", "cafe", "market", "temple", "heritage", "viewpoint", "other"]);

/** Blunt on purpose: a false positive costs a different destination, the reverse does not. */
const DISALLOWED = [
  "private", "residence", "residential", "apartment", "villa", "construction", "quarry",
  "landfill", "sewage", "military", "restricted", "trespass", "danger", "swimming",
  "river", "lake", "reservoir", "canal", "waterfall", "substation", "factory",
  "warehouse", "prison", "police", "hospital", "clinic", "school", "cemetery",
  "railway", "runway", "airfield",
];

function categorise(tags: Record<string, string>): string {
  const pairs: Array<[string, string, string]> = [
    ["natural", "beach", "beach"],
    ["leisure", "park", "park"],
    ["leisure", "garden", "park"],
    ["amenity", "cafe", "cafe"],
    ["amenity", "restaurant", "cafe"],
    ["amenity", "marketplace", "market"],
    ["amenity", "place_of_worship", "temple"],
    ["historic", "", "heritage"],
    ["tourism", "viewpoint", "viewpoint"],
    ["tourism", "museum", "heritage"],
  ];
  for (const [key, value, category] of pairs) {
    const tag = tags[key];
    if (tag && (!value || tag.startsWith(value))) return category;
  }
  return "other";
}

function isSafe(tags: Record<string, string>, category: string): boolean {
  const haystack = [category, tags.name, tags.amenity, tags.leisure, tags.tourism, tags.landuse]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  if (DISALLOWED.some((token) => haystack.includes(token))) return false;
  return PUBLIC_CATEGORIES.has(category);
}

/**
 * Ask Overpass for candidates. Returns an empty list on any failure, which the caller
 * treats as "use the built-in set" rather than an error.
 */
export async function findPlaces(spec: TripSpec, timeoutMs = 15000): Promise<Candidate[]> {
  if (spec.offline) return builtInPlaces(spec);

  const radius = Math.min(25000, Math.max(800, spec.rangeKm[1] * 1000 * 1.4));
  const selectors = spec.interests
    .map((interest) => OVERPASS_FILTERS[interest])
    .filter(Boolean)
    .slice(0, 4);
  if (!selectors.length) selectors.push('node["tourism"="viewpoint"]', 'node["amenity"="cafe"]');

  const query =
    `[out:json][timeout:25];\n(\n` +
    selectors.map((selector) => `  ${selector}(around:${Math.round(radius)},${spec.startLat},${spec.startLng});`).join("\n") +
    `\n);\nout center tags 60;`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch("https://overpass-api.de/api/interpreter", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: `data=${encodeURIComponent(query)}`,
      signal: controller.signal,
    });
    if (!response.ok) return builtInPlaces(spec);
    const payload = await response.json();
    const candidates: Candidate[] = [];
    for (const element of payload.elements ?? []) {
      const tags = element.tags ?? {};
      const name = tags.name ?? tags["name:en"];
      const lat = element.lat ?? element.center?.lat;
      const lng = element.lon ?? element.center?.lon;
      if (!name || lat === undefined || lng === undefined) continue;
      const category = categorise(tags);
      if (!isSafe(tags, category)) continue;
      candidates.push({
        id: `osm:${element.type}/${element.id}`,
        name: String(name),
        lat: Number(lat),
        lng: Number(lng),
        category,
        tags,
        distanceM: haversineM(spec.startLat, spec.startLng, Number(lat), Number(lng)),
        score: 0,
        reasons: [],
      });
    }
    if (!candidates.length) return builtInPlaces(spec);
    return candidates;
  } catch {
    return builtInPlaces(spec);
  } finally {
    clearTimeout(timer);
  }
}

/** A small deterministic set around the start, for offline and for demos. */
export function builtInPlaces(spec: TripSpec): Candidate[] {
  const ceiling = Math.min(spec.rangeKm[1] * 1000, 8000);
  const template: Array<[string, number, string, number]> = [
    ["The Reading Room", 0.22, "cafe", 40],
    ["Old Banyan Park", 0.48, "park", 120],
    ["Salt Wind Point", 0.86, "viewpoint", 200],
    ["Thursday Market", 0.4, "market", 260],
    ["The Long Shore", 0.99, "beach", 320],
    ["Ferry Cafe", 0.66, "cafe", 80],
    ["Heritage Wall", 0.34, "heritage", 150],
    ["Green Mile Garden", 0.3, "park", 60],
  ];
  return template.map(([name, fraction, category, bearing], index) => {
    const { lat, lng } = moveTowards(spec.startLat, spec.startLng, bearing, ceiling * fraction);
    return {
      id: `builtin:${index}`,
      name,
      lat,
      lng,
      category,
      tags: { name },
      distanceM: haversineM(spec.startLat, spec.startLng, lat, lng),
      score: 0,
      reasons: [],
    };
  });
}

// --------------------------------------------------------------------------- //
// scoring (the same weights the handheld uses)
// --------------------------------------------------------------------------- //
const INTEREST_WEIGHTS: Record<string, number> = {
  coffee: 1.0, cafe: 1.0, food: 1.2, nature: 1.1, park: 1.0, viewpoint: 1.4,
  beach: 1.5, market: 0.9, temple: 1.2, heritage: 1.3, museum: 1.1, bar: 0.7, bookshop: 0.6,
};

const RARITY: Record<string, number> = {
  beach: 1.0, viewpoint: 0.9, heritage: 0.9, temple: 0.8, market: 0.6, park: 0.6, cafe: 0.4, other: 0.3,
};

export function scoreCandidate(candidate: Candidate, spec: TripSpec): Candidate {
  const reasons: string[] = [];

  let interest = 0.3;
  for (const wanted of spec.interests) {
    if (wanted === candidate.category) {
      interest = Math.max(interest, (INTEREST_WEIGHTS[wanted] ?? 0.8) * 2);
      reasons.push(`you asked for ${wanted}`);
    }
  }

  const distanceKm = candidate.distanceM / 1000;
  const [low, high] = spec.rangeKm;
  let range: number;
  if (distanceKm >= low && distanceKm <= high) range = 2.0;
  else if (distanceKm < low) range = 0.9;
  else range = Math.max(0, 1.2 - ((distanceKm - high) / Math.max(1, high)) * 1.6);

  const speed = spec.transport === "walk" ? 1.35 : spec.transport === "bike" ? 4.2 : 9.5;
  const minutes = (distanceKm * 1000 * 1.28) / speed / 60;
  const time = minutes <= spec.maxMinutes ? 2.0 : Math.max(0, 1 - (minutes - spec.maxMinutes) / spec.maxMinutes);

  const rarity = RARITY[candidate.category] ?? 0.3;

  candidate.score = interest + range + time + rarity;
  candidate.reasons = reasons.length ? reasons : [`a good ${candidate.category}`];
  return candidate;
}

// --------------------------------------------------------------------------- //
// creative text from the SERVER's gateway
// --------------------------------------------------------------------------- //
interface PlanCopy {
  title: string;
  description: string;
  hints: Array<{ waypoint_idx: number; text: string }>;
  games: Array<{ type: string; title?: string; duration_min?: number; tasks: Array<{ text: string; item_key?: string; points?: number }> }>;
  arrival_line?: string;
  agent_greeting?: string;
}

const TEMPLATE_HINTS = [
  "Where the road starts to smell of the sea.",
  "Past the shop with the blue shutters.",
  "Where the paving stops and the ground goes soft.",
  "Follow the noise that is not traffic.",
  "Look for the wall with the old paint.",
  "Where two paths meet under a tree.",
];

const TEMPLATE_GAMES: Record<string, Array<{ text: string; item_key: string; points: number }>> = {
  photo_hunt: [
    { text: "Photograph something older than you.", item_key: "old", points: 15 },
    { text: "Photograph a reflection that is not a mirror.", item_key: "reflection", points: 20 },
    { text: "Photograph a door that looks like it has a story.", item_key: "door", points: 15 },
    { text: "Photograph a colour you cannot name quickly.", item_key: "colour", points: 25 },
    { text: "Photograph something exactly one hand wide.", item_key: "hand", points: 15 },
    { text: "Photograph a sign with a spelling mistake.", item_key: "sign", points: 20 },
  ],
  color_hunt: [
    { text: "Photograph something red", item_key: "red", points: 10 },
    { text: "Photograph something blue", item_key: "blue", points: 10 },
    { text: "Photograph something yellow", item_key: "yellow", points: 10 },
    { text: "Photograph something green", item_key: "green", points: 10 },
  ],
  funny_tasks: [
    { text: "Photograph your best 'I meant to do that' face.", item_key: "face", points: 15 },
    { text: "Find the most dramatic shadow and pose in it.", item_key: "shadow", points: 15 },
    { text: "Take a photo that looks like an album cover.", item_key: "album", points: 20 },
  ],
  bingo: [
    { text: "a red door", item_key: "r0c0", points: 10 },
    { text: "a cat", item_key: "r0c1", points: 10 },
    { text: "a bicycle", item_key: "r0c2", points: 10 },
    { text: "a hand-painted sign", item_key: "r0c3", points: 10 },
    { text: "a flower box", item_key: "r0c4", points: 10 },
    { text: "a bench with a view", item_key: "r1c0", points: 10 },
    { text: "a bird in flight", item_key: "r1c1", points: 10 },
    { text: "something blue", item_key: "r1c2", points: 10 },
    { text: "a cup of coffee", item_key: "r1c3", points: 10 },
    { text: "a poster older than a year", item_key: "r1c4", points: 10 },
  ],
};

function templateCopy(spec: TripSpec): { copy: PlanCopy; source: Plan["source"] } {
  const titles: Record<string, string[]> = {
    calm: ["A quiet line", "Slow miles"],
    chill: ["Salt and shade", "Nothing urgent"],
    playful: ["Silly by design", "Wrong turns welcome"],
    adventure: ["Past the last lamp", "The unknown bit"],
    romantic: ["Two and a road", "Late light"],
    foodie: ["Following the smell", "Snack navigation"],
  };
  const pool = titles[spec.mood] ?? titles.chill;
  return {
    copy: {
      title: pool[Math.floor(Math.random() * pool.length)],
      description: `A ${spec.mood} ${spec.transport} for ${spec.people}. The destination stays secret.`,
      hints: TEMPLATE_HINTS.map((text, index) => ({ waypoint_idx: index, text })),
      games: [
        {
          type: "photo_hunt",
          title: "Photo hunt",
          duration_min: Math.max(15, Math.min(45, Math.round(spec.maxMinutes / 2))),
          tasks: TEMPLATE_GAMES.photo_hunt,
        },
      ],
      arrival_line: "You found it. Take a minute.",
      agent_greeting: "Ready when you are.",
    },
    source: spec.offline ? "offline-template" : "template",
  };
}

/**
 * Ask the server's LLM gateway for the words.
 *
 * Nothing about the place is included: only the mood, group, mode, duration, distance
 * and category. A 503 (no key configured on the server) is a normal answer and falls
 * back to the template.
 */
export async function fetchCopy(spec: TripSpec, distanceKm: number, category: string): Promise<{ copy: PlanCopy; source: Plan["source"]; reason: string }> {
  const base = templateCopy(spec);
  if (spec.offline) return { ...base, reason: "running offline" };

  try {
    const response = await api.post<{ text: string; provider: string }>(
      "/api/llm/plan",
      {
        mood: spec.mood,
        people: spec.people,
        transport: spec.transport,
        max_minutes: spec.maxMinutes,
        distance_km: distanceKm,
        category,
        interests: spec.interests,
        food_exclusions: spec.foodExclusions,
        players: Array.from({ length: spec.people }, (_, index) => `Explorer ${index + 1}`),
        games: ["photo_hunt"],
      },
      { noRetry: true }
    );
    const parsed = JSON.parse(response.text) as PlanCopy;
    if (!parsed?.title || !Array.isArray(parsed.hints)) throw new Error("the gateway returned an unusable shape");
    // Fill any gaps from the template so a terse answer cannot cost us the game items.
    return {
      copy: {
        ...base.copy,
        ...parsed,
        hints: parsed.hints.length ? parsed.hints : base.copy.hints,
        games: parsed.games?.length ? parsed.games : base.copy.games,
      },
      source: "llm",
      reason: "",
    };
  } catch (error) {
    const message = (error as any)?.data?.detail?.error === "llm_unavailable"
      ? "the server has no LLM key configured, so I wrote this myself"
      : `the server could not write the trip (${(error as Error).message})`;
    return { ...base, reason: message };
  }
}

// --------------------------------------------------------------------------- //
// assembling the plan
// --------------------------------------------------------------------------- //
function buildWaypoints(spec: TripSpec, destination: Candidate, hints: string[]): PlanWaypoint[] {
  const total = haversineM(spec.startLat, spec.startLng, destination.lat, destination.lng);
  if (total < 250) return [];

  const count = Math.max(2, Math.min(6, Math.floor(total / 700) + 2));
  const kinds: PlanWaypoint["kind"][] = ["photo_spot", "route", "shop", "viewpoint", "route", "food"];
  const routeBearing = bearingDeg(spec.startLat, spec.startLng, destination.lat, destination.lng);

  const waypoints: PlanWaypoint[] = [];
  for (let index = 0; index < count; index += 1) {
    const fraction = (index + 1) / (count + 1);
    const base = interpolate(spec.startLat, spec.startLng, destination.lat, destination.lng, fraction);
    // Nudge sideways so the route bends like a real walk instead of a ruler line.
    const side = index % 2 === 0 ? 90 : -90;
    const offset = Math.min(90, total * 0.04) * (1 + 0.3 * index);
    const moved = moveTowards(base.lat, base.lng, routeBearing + side, offset);
    const kind = kinds[index % kinds.length];
    waypoints.push({
      id: `w${index}`,
      idx: index,
      lat: moved.lat,
      lng: moved.lng,
      kind,
      hint: hints[index % hints.length] ?? "",
      reveal: (kind === "route" ? "on_proximity" : "on_proximity") as RevealRule,
      radius_m: kind === "photo_spot" ? 45 : 60,
    });
  }
  return waypoints;
}

export interface PlanResult {
  plan: Plan;
  candidates: Candidate[];
  source: Plan["source"];
  reason: string;
}

/**
 * Produce a plan. Always returns one: if the map is unreachable it uses the built-in
 * set, and if the LLM is unreachable it writes the copy itself.
 */
export async function planTrip(rawText: string, startLat: number, startLng: number, offline = false): Promise<PlanResult> {
  const spec = parseSpec(rawText, startLat, startLng, offline);

  const found = await findPlaces(spec);
  const scored = found
    .map((candidate) => scoreCandidate(candidate, spec))
    // Range filter first: the brief's "simplest option" rule and the user's own words
    // beat a charming place 8 km away.
    .filter((candidate) => candidate.distanceM / 1000 <= Math.max(spec.rangeKm[1] * 1.25, spec.rangeKm[0]))
    .sort((a, b) => b.score - a.score || a.distanceM - b.distanceM);

  const winner = scored[0];
  if (!winner) {
    throw new Error("I could not find anywhere safe to send you. Try a different area or a longer trip.");
  }

  const distanceKm = winner.distanceM / 1000;
  const { copy, source, reason } = await fetchCopy(spec, distanceKm, winner.category);
  const hints = copy.hints
    .slice()
    .sort((a, b) => a.waypoint_idx - b.waypoint_idx)
    .map((hint) => hint.text);
  const waypoints = buildWaypoints(spec, winner, hints.length ? hints : TEMPLATE_HINTS);
  // Attach the model's hint text to the waypoint it was written for.
  copy.hints.forEach((hint, index) => {
    if (waypoints[index]) waypoints[index].hint = hint.text;
  });

  const routeM = pathLengthM([
    [spec.startLat, spec.startLng],
    ...waypoints.map((waypoint) => [waypoint.lat, waypoint.lng] as [number, number]),
    [winner.lat, winner.lng],
  ]);

  const plan: Plan = {
    schema_version: 1,
    trip_id: cryptoId(),
    title: copy.title,
    description: copy.description,
    mood: spec.mood,
    members: Array.from({ length: spec.people }, (_, index) => ({
      name: `Explorer ${index + 1}`,
      color: ["#FFD400", "#1F4B8F", "#2E7D32", "#7B4FC2", "#C2185B", "#EF6C00"][index % 6],
    })),
    destination: {
      name: winner.name,
      lat: winner.lat,
      lng: winner.lng,
      category: winner.category,
      radius_m: 60,
      secret: true,
    },
    waypoints,
    games: (copy.games ?? []).slice(0, 3).map((game) => ({
      type: game.type,
      tasks: (game.tasks ?? []).map((task) => task.text),
      duration_min: game.duration_min ?? 30,
    })),
    route_km: Math.round((routeM / 1000) * 100) / 100,
    fallback: { used: source !== "llm", reason },
    created_at: new Date().toISOString(),
    source,
  };

  return { plan, candidates: scored.slice(0, 5), source, reason };
}

function cryptoId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) return crypto.randomUUID();
  return `t_${Date.now()}_${Math.random().toString(16).slice(2, 10)}`;
}

// --------------------------------------------------------------------------- //
// the payload the device API expects
// --------------------------------------------------------------------------- //
export function toServerPlan(plan: Plan): Record<string, unknown> {
  return {
    trip_id: plan.trip_id,
    title: plan.title,
    description: plan.description,
    mood: plan.mood,
    calendar_at: plan.created_at,
    members: plan.members,
    destination: {
      name: plan.destination.name,
      lat: plan.destination.lat,
      lng: plan.destination.lng,
      category: plan.destination.category,
      radius_m: plan.destination.radius_m,
      secret: true,
    },
    waypoints: plan.waypoints.map((waypoint) => ({
      idx: waypoint.idx,
      lat: waypoint.lat,
      lng: waypoint.lng,
      radius_m: waypoint.radius_m,
      hint: waypoint.hint,
      kind: waypoint.kind,
      reveal: waypoint.reveal,
    })),
    games: plan.games,
    schema_version: plan.schema_version,
  };
}
