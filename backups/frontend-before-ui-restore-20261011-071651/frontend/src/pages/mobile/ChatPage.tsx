import React, { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowUp, Mic, Sparkles } from "lucide-react";
import { ConnectionBanner } from "../../components/mobile/ConnectionBanner";
import { ProgressLine, useAutoScroll } from "../../components/mobile/CompassDial";
import { planTrip } from "../../lib/planner";
import { tripEngine } from "../../lib/engine";
import { getCurrentFix, requestLocationPermission, tapFeedback } from "../../lib/native";
import { useAppStore, type ChatMessage } from "../../lib/appStore";

/**
 * The Chat screen: the agent persona and the trip planner in one conversation.
 *
 * This is the "AI Chat" reference screen, and the same screen does the planning. The
 * important product decisions here:
 *
 * * **The agent never names a place before the reveal.** Every line it can produce is
 *   either fixed copy, a revealed hint, or the engine's coaching line, all of which are
 *   leak-free by construction. There is no "ask the model anything" path, because that
 *   is the one thing that could give the destination away.
 * * **Voice is optional.** The composer takes typing and the suggestion chips take a
 *   tap, so the app is fully usable with the microphone refused or missing.
 * * **Planning works offline.** If the map or the LLM is unreachable the planner falls
 *   back to a built-in place set and its own copy, and says so in the transcript rather
 *   than pretending.
 */

const SUGGESTIONS = [
  "a chill walk for two, about an hour",
  "somewhere with coffee and a view",
  "a playful trip for four, 2 hours",
  "a quiet green walk, under 3 km",
  "a sunset walk for two",
];

export const ChatPage: React.FC = () => {
  const messages = useAppStore((state) => state.messages);
  const addMessage = useAppStore((state) => state.addMessage);
  const pendingRequest = useAppStore((state) => state.pendingRequest);
  const setPendingRequest = useAppStore((state) => state.setPendingRequest);
  const setTab = useAppStore((state) => state.setTab);

  const [draft, setDraft] = useState("");
  const [planning, setPlanning] = useState(false);
  const [progress, setProgress] = useState(0);
  const [step, setStep] = useState("");
  const [listening, setListening] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const recognitionRef = useRef<any>(null);
  const scroller = useAutoScroll<HTMLDivElement>(messages.length);

  // Greet once, so the screen is never an empty box.
  useEffect(() => {
    if (!useAppStore.getState().messages.length) {
      addMessage({
        role: "agent",
        text: "Tell me the mood, who is coming and how long you have. I will pick somewhere and keep it a secret until you arrive.",
      });
    }
  }, [addMessage]);

  // Pick up a request handed over from another screen.
  useEffect(() => {
    if (!pendingRequest) return;
    setDraft(pendingRequest);
    setPendingRequest(null);
  }, [pendingRequest, setPendingRequest]);

  const send = async (text: string) => {
    const request = text.trim();
    if (!request || planning) return;
    setDraft("");
    setError(null);
    void tapFeedback("light");
    addMessage({ role: "me", text: request });
    setPlanning(true);
    setProgress(0.08);
    setStep("reading what you asked for");

    try {
      // A fix is needed to plan anywhere sensible. Ask for one, but do not block the
      // whole feature on it: if it is refused the user can still plan from a default.
      setProgress(0.14);
      setStep("finding you");
      let lat = 11.2588;
      let lng = 75.7804;
      let located = false;
      try {
        await requestLocationPermission();
        const fix = await getCurrentFix({ timeoutMs: 12000 });
        lat = fix.lat;
        lng = fix.lng;
        located = true;
      } catch (locationError) {
        addMessage({
          role: "system",
          text: `No GPS fix (${(locationError as Error).message}). Planning from the demo area instead.`,
        });
      }

      setProgress(0.3);
      setStep("looking for places nearby");
      const result = await planTrip(request, lat, lng, false);

      setProgress(0.85);
      setStep("writing the hints");
      tripEngine.setPlan(result.plan);

      setProgress(1);
      addMessage({
        role: "agent",
        text: `${result.plan.title}\n\n${result.plan.description}\n\nAbout ${result.plan.route_km} km, ${result.plan.waypoints.length} waypoints. Follow the arrow when you are ready.`,
      });
      if (result.source !== "llm") {
        addMessage({
          role: "system",
          text: result.reason
            ? `The words are mine, not the model's: ${result.reason}. The trip still works.`
            : "The words are mine, not the model's. The trip still works.",
        });
      }
      if (!located) {
        addMessage({
          role: "system",
          text: "Without a GPS fix the route starts from a default point. Turn on location for an accurate trip.",
        });
      }
      setTab("compass");
    } catch (planError) {
      const message = (planError as Error).message || "Something went wrong planning that.";
      setError(message);
      addMessage({ role: "agent", text: `I could not plan that: ${message}` });
    } finally {
      setPlanning(false);
      setStep("");
      setProgress(0);
    }
  };

  /** Voice input through the browser's own recogniser: no key, no model to ship. */
  const startListening = async () => {
    const SpeechRecognition =
      (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SpeechRecognition) {
      addMessage({
        role: "system",
        text: "This phone's browser has no speech recognition. Type the request, or tap a suggestion.",
      });
      return;
    }
    try {
      const recognition = new SpeechRecognition();
      recognition.lang = "en-IN";
      recognition.interimResults = true;
      recognition.continuous = false;
      recognition.onresult = (event: any) => {
        const transcript = Array.from(event.results)
          .map((result: any) => result[0].transcript)
          .join(" ");
        setDraft(transcript);
      };
      recognition.onerror = (event: any) => {
        setListening(false);
        addMessage({ role: "system", text: `The microphone gave up: ${event.error}. Type it instead.` });
      };
      recognition.onend = () => setListening(false);
      recognitionRef.current = recognition;
      recognition.start();
      setListening(true);
      void tapFeedback("medium");
    } catch {
      setListening(false);
      addMessage({ role: "system", text: "Could not start the microphone. Type the request instead." });
    }
  };

  return (
    <>
      <div className="ffh-scroll" ref={scroller}>
        <ConnectionBanner />

        <header style={{ padding: "18px 20px 4px" }}>
          <h1 className="ffh-title">Chat</h1>
          <p className="ffh-subtitle">Tell me what kind of trip you want</p>
        </header>

        <section className="ffh-chat" style={{ padding: "14px 20px 150px" }}>
          {messages.map((message) => (
            <Bubble key={message.id} message={message} />
          ))}

          {planning && (
            <div className="ffh-card" style={{ padding: 18 }}>
              <div className="ffh-row" style={{ gap: 12 }}>
                <span className="ffh-spinner" />
                <div className="ffh-grow">
                  <div style={{ fontWeight: 700, fontSize: 15 }}>{step || "working"}</div>
                  <div className="ffh-muted" style={{ marginTop: 8 }}>
                    <ProgressLine value={progress} />
                  </div>
                </div>
              </div>
            </div>
          )}

          {error && (
            <div className="ffh-banner ffh-banner--bad">
              <span>{error}</span>
            </div>
          )}

          {!planning && (
            <div className="ffh-chips" style={{ marginTop: 6 }}>
              {SUGGESTIONS.map((suggestion) => (
                <button
                  key={suggestion}
                  type="button"
                  className="ffh-chip ffh-haptic-tap"
                  onClick={() => void send(suggestion)}
                >
                  <Sparkles size={15} /> {suggestion}
                </button>
              ))}
            </div>
          )}
        </section>
      </div>

      <form
        className="ffh-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void send(draft);
        }}
      >
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={listening ? "Listening..." : "A chill walk for two, about an hour"}
          aria-label="Describe the trip"
        />
        <button
          type="button"
          className={`ffh-mic ffh-haptic-tap${listening ? " ffh-mic--listening" : ""}`}
          onClick={() => (listening ? recognitionRef.current?.stop() : void startListening())}
          aria-label="Speak the request"
        >
          <Mic size={19} />
        </button>
        <button type="submit" className="ffh-mic ffh-haptic-tap" disabled={!draft.trim() || planning} aria-label="Send">
          <ArrowUp size={19} />
        </button>
      </form>
    </>
  );
};

const Bubble: React.FC<{ message: ChatMessage }> = ({ message }) => {
  if (message.role === "system") {
    return (
      <div className="ffh-banner ffh-banner--warn" style={{ alignSelf: "stretch" }}>
        <span>{message.text}</span>
      </div>
    );
  }
  const className =
    message.role === "me" ? "ffh-bubble ffh-bubble--me" : message.role === "hint" ? "ffh-bubble ffh-bubble--hint" : "ffh-bubble ffh-bubble--agent";
  return (
    <div className={className} style={{ whiteSpace: "pre-wrap" }}>
      {message.text}
    </div>
  );
};

/** A link strip shown when a plan exists but the user is on the Chat screen. */
export const TripShortcut: React.FC = () => (
  <Link to="/" className="ffh-link">
    Back to the compass
  </Link>
);
