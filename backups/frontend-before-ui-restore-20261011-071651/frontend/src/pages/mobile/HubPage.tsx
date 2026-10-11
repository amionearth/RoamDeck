import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  BarChart3,
  BookImage,
  Camera,
  Check,
  Grid3x3,
  HelpCircle,
  Loader2,
  Palette,
  QrCode,
  ScanLine,
  Settings,
  Sparkles,
  Ticket,
  Trophy,
  Users,
} from "lucide-react";
import { ConnectionBanner } from "../../components/mobile/ConnectionBanner";
import { api } from "../../lib/api";
import { tapFeedback } from "../../lib/native";
import { useAppStore } from "../../lib/appStore";

/**
 * The Hub: the QR game host, the mini-games, and the way into the social side.
 *
 * This is the "QR & Games Hub" reference screen. Two things it does that the reference
 * does not show, because they are what makes it work in the real world:
 *
 * * **Hosting, not just joining.** The app creates a game session on the server and
 *   draws the QR on its own screen, so other people scan *this phone* and upload from
 *   their browser. That is brief 02 Part B, and it is the flow that actually needs a
 *   native app: the phone has the camera, the GPS and the offline queue.
 * * **A typed code as well as a scan.** A QR that will not scan (a cracked screen, a
 *   dirty lens, a bright afternoon) must not stop the game, so the join code is always
 *   readable and typeable.
 */

interface GameCard {
  key: string;
  title: string;
  subtitle: string;
  icon: React.ComponentType<{ size?: number; strokeWidth?: number }>;
  /** The server's `type` value for a session. */
  type: string;
  minPlayers: number;
  maxPlayers: number;
  defaultMinutes: number;
}

/** The games this app can host. Content comes from the server; the shell comes from here. */
export const GAME_CARDS: GameCard[] = [
  {
    key: "photo_hunt",
    title: "Photo hunt",
    subtitle: "Find and shoot",
    icon: Camera,
    type: "photo_hunt",
    minPlayers: 1,
    maxPlayers: 8,
    defaultMinutes: 30,
  },
  {
    key: "bingo",
    title: "Bingo",
    subtitle: "Five in a row",
    icon: Grid3x3,
    type: "bingo",
    minPlayers: 2,
    maxPlayers: 8,
    defaultMinutes: 30,
  },
  {
    key: "color_hunt",
    title: "Colour hunt",
    subtitle: "Match the colour",
    icon: Palette,
    type: "color_hunt",
    minPlayers: 1,
    maxPlayers: 6,
    defaultMinutes: 15,
  },
  {
    key: "funny_tasks",
    title: "Silly tasks",
    subtitle: "Silly missions",
    icon: HelpCircle,
    type: "funny_tasks",
    minPlayers: 2,
    maxPlayers: 10,
    defaultMinutes: 30,
  },
];

const SOCIAL_LINKS = [
  { to: "/feed", label: "Feed", icon: Sparkles },
  { to: "/passport", label: "Passport", icon: BookImage },
  { to: "/leaderboard", label: "Boards", icon: BarChart3 },
  { to: "/games", label: "My games", icon: Trophy },
  { to: "/friends", label: "Friends", icon: Users },
  { to: "/settings", label: "Settings", icon: Settings },
];

interface LiveSession {
  game_id: string;
  code: string;
  join_url: string;
  qr_png_url?: string;
  status: string;
  ends_at?: string | null;
}

export const HubPage: React.FC = () => {
  const queueVersion = useAppStore((state) => state.queueVersion);
  const bumpQueue = useAppStore((state) => state.bumpQueue);
  const [session, setSession] = useState<LiveSession | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [joinCode, setJoinCode] = useState("");
  const [players, setPlayers] = useState<Array<{ name: string; joined: boolean; score: number }>>([]);
  void queueVersion;

  const serverOrigin = session ? new URL(session.join_url).origin : "";

  /** Poll the lobby while a session is open, so the player list fills in live. */
  useEffect(() => {
    if (!session || session.status === "closed") return;
    let cancelled = false;

    const poll = async () => {
      try {
        const state = await api.get<any>(`/api/device/games/${session.game_id}/state`);
        if (cancelled) return;
        setPlayers(
          (state.players ?? []).map((player: any) => ({
            name: player.display_name ?? player.name,
            joined: Boolean(player.joined),
            score: player.score ?? 0,
          }))
        );
        if (state.status === "closed") {
          setSession((current) => (current ? { ...current, status: "closed" } : current));
        }
      } catch {
        /* the banner already reports connectivity */
      }
    };

    void poll();
    const timer = window.setInterval(poll, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [session]);

  const host = async (card: GameCard) => {
    setBusy(card.key);
    setError(null);
    void tapFeedback("medium");
    try {
      const gameId =
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `g_${Date.now()}`;
      const created = await api.post<LiveSession>("/api/device/games", {
        game_id: gameId,
        type: card.type,
        duration_s: card.defaultMinutes * 60,
        params: {
          approval: card.type === "color_hunt" ? "auto" : "peer_vote",
          win_condition: card.type === "bingo" ? "first_n_in_row" : "most_points",
          n: 5,
          penalty: "loser buys a treat",
        },
        players: [
          { name: "Explorer 1", color: "#FFD400" },
          { name: "Explorer 2", color: "#1F4B8F" },
        ],
        tasks: [
          { text: "Photograph something that stands out", item_key: "red", points: 10 },
          { text: "Photograph something with a pattern", item_key: "pattern", points: 10 },
        ],
      });
      setSession({ ...created, status: created.status ?? "open" });
      setPlayers([]);
      bumpQueue();
    } catch (hostError) {
      const message =
        (hostError as any)?.data?.detail?.message ??
        (hostError as Error).message ??
        "Could not create the game.";
      setError(
        `${message} Check that this phone is paired as a handheld (Settings) and that the server is reachable.`
      );
    } finally {
      setBusy(null);
    }
  };

  const start = async () => {
    if (!session) return;
    setBusy("start");
    try {
      const result = await api.post<{ ends_at: string }>(`/api/device/games/${session.game_id}/start`);
      setSession({ ...session, status: "running", ends_at: result.ends_at });
    } catch (startError) {
      setError((startError as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const end = async () => {
    if (!session) return;
    setBusy("end");
    try {
      await api.post(`/api/device/games/${session.game_id}/end`);
      setSession({ ...session, status: "closed" });
    } catch (endError) {
      setError((endError as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const joinByCode = () => {
    const code = joinCode.trim().toUpperCase();
    if (code.length < 4) return;
    window.location.hash = `#/g/${code}`;
    setJoinCode("");
  };

  return (
    <div className="ffh-scroll">
      <ConnectionBanner />

      <header style={{ padding: "18px 20px 0", display: "flex", alignItems: "flex-start", gap: 14 }}>
        <div className="ffh-grow">
          <h1 className="ffh-title">Hub</h1>
          <p className="ffh-subtitle">QR scanner & games</p>
        </div>
        <Link to="/settings" className="ffh-icon-btn" aria-label="Settings">
          <Settings size={20} />
        </Link>
      </header>

      {/* ------------------------------------------------------ host a game */}
      {!session && (
        <section style={{ padding: "20px 20px 0" }}>
          <div className="ffh-cta">
            <span className="ffh-game-card__icon" style={{ width: 62, height: 62 }}>
              <QrCode size={28} />
            </span>
            <div>
              <div style={{ fontSize: 20, fontWeight: 800 }}>Host a game</div>
              <div className="ffh-muted" style={{ marginTop: 4 }}>
                Pick a game below. This phone shows the QR code and the others scan it with their camera.
              </div>
            </div>
          </div>

          {error && (
            <div className="ffh-banner ffh-banner--bad" style={{ marginTop: 14 }}>
              <span>{error}</span>
            </div>
          )}

          <h2 className="ffh-section">Mini games</h2>
          <div className="ffh-grid-2">
            {GAME_CARDS.map((card) => (
              <button
                key={card.key}
                type="button"
                className="ffh-game-card ffh-haptic-tap"
                onClick={() => void host(card)}
                disabled={busy !== null}
              >
                <span className="ffh-game-card__icon">
                  {busy === card.key ? <Loader2 size={24} className="ffh-spin" /> : <card.icon size={24} />}
                </span>
                <span className="ffh-game-card__title">{card.title}</span>
                <span className="ffh-game-card__sub">
                  {busy === card.key ? "Creating..." : `${card.subtitle} · ${card.defaultMinutes} min`}
                </span>
              </button>
            ))}
          </div>
        </section>
      )}

      {/* ------------------------------------------------------- the session */}
      {session && (
        <section style={{ padding: "20px 20px 0" }}>
          <div className="ffh-card" style={{ textAlign: "center" }}>
            <div className="ffh-row ffh-row--between" style={{ marginBottom: 8 }}>
              <span className={`ffh-pill ${session.status === "closed" ? "ffh-pill--bad" : session.status === "running" ? "ffh-pill--good" : ""}`}>
                {session.status === "closed" ? "Game over" : session.status === "running" ? "Running" : "Lobby"}
              </span>
              <span className="ffh-pill">{players.filter((player) => player.joined).length} joined</span>
            </div>

            {session.status === "closed" ? (
              <>
                <div style={{ fontSize: 22, fontWeight: 800, marginTop: 10 }}>This game has ended</div>
                <p className="ffh-muted" style={{ marginTop: 6 }}>
                  The photos are in the trip album on the website. Scanning the old code now shows the same message.
                </p>
                <button
                  type="button"
                  className="ffh-btn ffh-btn--block"
                  style={{ marginTop: 14 }}
                  onClick={() => setSession(null)}
                >
                  Host another
                </button>
              </>
            ) : (
              <>
                <div style={{ fontSize: 15, fontWeight: 700, marginTop: 4 }}>Scan to join</div>
                <img
                  src={`${serverOrigin}${session.qr_png_url ?? `/api/games/${session.game_id}/qr.png`}`}
                  alt="QR code to join the game"
                  width={230}
                  height={230}
                  style={{ margin: "14px auto 0", display: "block", background: "#fff", borderRadius: 16, padding: 10 }}
                />
                <div className="ffh-mono" style={{ fontSize: 24, fontWeight: 800, marginTop: 12, letterSpacing: "0.16em" }}>
                  {session.code}
                </div>
                <div className="ffh-muted" style={{ marginTop: 6, wordBreak: "break-all", fontSize: 13 }}>
                  {session.join_url.replace(/^https?:\/\//, "")}
                </div>
                <div className="ffh-banner ffh-banner--warn" style={{ marginTop: 12, textAlign: "left" }}>
                  <span>Phones must be on the same WiFi as this phone, with mobile data off.</span>
                </div>

                <div style={{ textAlign: "left", marginTop: 16 }}>
                  {players.length === 0 && <div className="ffh-muted">Nobody has joined yet.</div>}
                  {players.map((player) => (
                    <div key={player.name} className="ffh-row ffh-row--between" style={{ padding: "8px 0" }}>
                      <span style={{ fontWeight: 600 }}>
                        <Check
                          size={15}
                          style={{ color: player.joined ? "var(--ffh-success)" : "var(--ffh-text-faint)", marginRight: 8 }}
                        />
                        {player.name}
                      </span>
                      <span className="ffh-mono ffh-muted">{player.score} pts</span>
                    </div>
                  ))}
                </div>

                <div className="ffh-row" style={{ gap: 10, marginTop: 16 }}>
                  {session.status === "open" ? (
                    <button type="button" className="ffh-btn ffh-btn--block" onClick={start} disabled={busy !== null}>
                      {busy === "start" ? "Starting..." : "Start the game"}
                    </button>
                  ) : (
                    <button type="button" className="ffh-btn ffh-btn--danger ffh-btn--block" onClick={end} disabled={busy !== null}>
                      {busy === "end" ? "Ending..." : "End the game"}
                    </button>
                  )}
                </div>
                <button
                  type="button"
                  className="ffh-btn ffh-btn--ghost ffh-btn--block"
                  style={{ marginTop: 10 }}
                  onClick={() => setSession(null)}
                >
                  Close this session
                </button>
              </>
            )}
          </div>
        </section>
      )}

      {/* --------------------------------------------------------- join a game */}
      <section style={{ padding: "22px 20px 0" }}>
        <h2 className="ffh-section">Join someone else's game</h2>
        <div className="ffh-card">
          <div className="ffh-row" style={{ gap: 12 }}>
            <span className="ffh-game-card__icon" style={{ width: 44, height: 44, flex: "none" }}>
              <ScanLine size={20} />
            </span>
            <div className="ffh-grow">
              <div style={{ fontWeight: 700, fontSize: 15 }}>Type the join code</div>
              <div className="ffh-muted" style={{ marginTop: 2 }}>
                Five letters, shown on the other phone's screen.
              </div>
            </div>
          </div>
          <div className="ffh-row" style={{ gap: 10, marginTop: 14 }}>
            <input
              value={joinCode}
              onChange={(event) => setJoinCode(event.target.value.toUpperCase())}
              placeholder="QX7K2"
              maxLength={8}
              className="ffh-mono"
              style={{
                flex: 1,
                background: "var(--ffh-surface-2)",
                border: "1px solid var(--ffh-border)",
                borderRadius: 14,
                padding: "14px 16px",
                color: "var(--ffh-text)",
                fontSize: 20,
                letterSpacing: "0.14em",
                outline: "none",
                textTransform: "uppercase",
              }}
            />
            <button type="button" className="ffh-btn" onClick={joinByCode} disabled={joinCode.trim().length < 4}>
              Join
            </button>
          </div>
        </div>
      </section>

      {/* ----------------------------------------------------------- the rest */}
      <section style={{ padding: "22px 20px 0" }}>
        <h2 className="ffh-section">
          <Ticket size={18} /> Everything else
        </h2>
        <div className="ffh-grid-2">
          {SOCIAL_LINKS.map(({ to, label, icon: Icon }) => (
            <Link
              key={to}
              to={to}
              className="ffh-game-card ffh-haptic-tap"
              style={{ textDecoration: "none", color: "inherit", minHeight: 116 }}
            >
              <span className="ffh-game-card__icon">
                <Icon size={22} />
              </span>
              <span className="ffh-game-card__title">{label}</span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
};
