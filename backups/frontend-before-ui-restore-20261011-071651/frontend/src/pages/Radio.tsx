import React, { useState, useEffect, useRef } from "react";
import { Mic, MicOff, Volume2, Radio, Wifi, Users, ShieldAlert, Sparkles } from "lucide-react";
import { realtime } from "../lib/ws";
import { useAuth } from "../lib/auth";

export const RadioPage: React.FC = () => {
  const { user } = useAuth();
  const [channel, setChannel] = useState("radio:ch1");
  const [isTalking, setIsTalking] = useState(false);
  const [isReceiving, setIsReceiving] = useState(false);
  const [activeSpeaker, setActiveSpeaker] = useState<string | null>(null);
  const [transmissions, setTransmissions] = useState<Array<{ id: string; sender: string; time: string; blobUrl?: string }>>([]);
  const [statusMessage, setStatusMessage] = useState("Connected to local Wi-Fi radio network.");
  const [micPermitted, setMicPermitted] = useState<boolean | null>(null);

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const speakerAudioRef = useRef<HTMLAudioElement | null>(null);

  const callsign = user?.display_name || `Explorer-${Math.floor(100 + Math.random() * 900)}`;

  useEffect(() => {
    realtime.connect();
    realtime.subscribe(channel);

    const unsubSignal = realtime.on("radio.signal", (data) => {
      if (data.sender !== callsign) {
        if (data.status === "transmitting") {
          setIsReceiving(true);
          setActiveSpeaker(data.sender);
        } else {
          setIsReceiving(false);
          setActiveSpeaker(null);
        }
      }
    });

    const unsubAudio = realtime.on("radio.transmission", (data) => {
      if (data.sender !== callsign && data.audioBase64) {
        setIsReceiving(false);
        setActiveSpeaker(null);

        // Play incoming audio broadcast
        try {
          const byteCharacters = atob(data.audioBase64);
          const byteNumbers = new Array(byteCharacters.length);
          for (let i = 0; i < byteCharacters.length; i++) {
            byteNumbers[i] = byteCharacters.charCodeAt(i);
          }
          const byteArray = new Uint8Array(byteNumbers);
          const blob = new Blob([byteArray], { type: data.mimeType || "audio/webm" });
          const blobUrl = URL.createObjectURL(blob);

          if (speakerAudioRef.current) {
            speakerAudioRef.current.src = blobUrl;
            speakerAudioRef.current.play().catch((e) => console.log("Audio play error:", e));
          }

          setTransmissions((prev) => [
            {
              id: Date.now().toString(),
              sender: data.sender,
              time: new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
              blobUrl,
            },
            ...prev.slice(0, 15),
          ]);
        } catch (e) {
          console.error("Audio decode error:", e);
        }
      }
    });

    return () => {
      realtime.unsubscribe(channel);
      unsubSignal();
      unsubAudio();
    };
  }, [channel, callsign]);

  const activeStreamRef = useRef<MediaStream | null>(null);

  // Explicit permission request for mobile phones
  const requestMicPermission = async () => {
    try {
      setStatusMessage("Requesting microphone access from browser...");
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      activeStreamRef.current = stream;
      setMicPermitted(true);
      setStatusMessage("Microphone enabled! Hold button to broadcast.");
    } catch (err: any) {
      console.warn("Permission denied or unavailable:", err);
      setMicPermitted(false);
      setStatusMessage("Microphone permission denied. Tap 'Allow' when prompted.");
    }
  };

  // Check permission state on mount
  useEffect(() => {
    if (navigator.permissions && navigator.permissions.query) {
      navigator.permissions.query({ name: "microphone" as PermissionName }).then((res) => {
        if (res.state === "granted") {
          setMicPermitted(true);
        } else if (res.state === "denied") {
          setMicPermitted(false);
        }
      }).catch(() => {});
    }
  }, []);

  // Request audio permission and start recording
  const startTransmitting = async () => {
    try {
      setStatusMessage("Opening audio transmission...");
      let stream = activeStreamRef.current;
      if (!stream || !stream.active) {
        stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        activeStreamRef.current = stream;
        setMicPermitted(true);
      }

      const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : (MediaRecorder.isTypeSupported("audio/mp4") ? "audio/mp4" : "audio/webm");

      const recorder = new MediaRecorder(stream, { mimeType });
      mediaRecorderRef.current = recorder;
      audioChunksRef.current = [];

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audioChunksRef.current.push(event.data);
        }
      };

      recorder.onstop = async () => {
        const audioBlob = new Blob(audioChunksRef.current, { type: mimeType });
        const reader = new FileReader();
        reader.readAsDataURL(audioBlob);
        reader.onloadend = () => {
          const base64Data = (reader.result as string).split(",")[1];
          // Transmit audio chunk via WebSocket
          realtime.send({
            event: "radio.transmission",
            channel,
            data: {
              sender: callsign,
              mimeType,
              audioBase64: base64Data,
            },
          });
          // Clear signal
          realtime.send({
            event: "radio.signal",
            channel,
            data: { sender: callsign, status: "idle" },
          });
        };
      };

      recorder.start();
      setIsTalking(true);
      setStatusMessage("BROADCASTING LIVE (Release to send)...");

      // Signal others that this user is transmitting
      realtime.send({
        event: "radio.signal",
        channel,
        data: { sender: callsign, status: "transmitting" },
      });
    } catch (err: any) {
      console.warn("Microphone access error:", err);
      setMicPermitted(false);
      setStatusMessage("Mic unavailable (tap 'Enable Microphone' below to grant access).");
      setIsTalking(false);
    }
  };

  const stopTransmitting = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === "recording") {
      mediaRecorderRef.current.stop();
      setIsTalking(false);
      setStatusMessage("Transmission sent over Wi-Fi.");
    }
  };

  return (
    <div className="max-w-xl mx-auto space-y-6 pb-24 animate-fade-in">
      <audio ref={speakerAudioRef} className="hidden" autoPlay />

      {/* Header */}
      <div className="glass rounded-[32px] p-6 sm:p-8 border border-white/40 shadow-xl relative overflow-hidden">
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <div className="w-12 h-12 rounded-2xl bg-teal-800 text-teal-100 flex items-center justify-center shadow-md">
              <Radio className="w-6 h-6 animate-pulse" />
            </div>
            <div>
              <h1 className="font-serif text-2xl sm:text-3xl text-ink font-normal">
                Wi-Fi Walkie-Talkie
              </h1>
              <p className="text-xs text-ink-muted font-sans mt-0.5">
                Instant peer-to-peer audio link between phones and laptop
              </p>
            </div>
          </div>

          <div className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-emerald-500/15 border border-emerald-500/30 text-emerald-800 text-xs font-mono font-medium">
            <Wifi className="w-3.5 h-3.5" />
            <span>LAN Wi-Fi</span>
          </div>
        </div>

        {/* Channel Selector */}
        <div className="grid grid-cols-3 gap-2 p-1.5 rounded-2xl glass-strong border border-white/40 mb-6">
          {[
            { id: "radio:ch1", label: "Channel 1", desc: "Expedition Main" },
            { id: "radio:ch2", label: "Channel 2", desc: "Game Crew" },
            { id: "radio:ch3", label: "Channel 3", desc: "Scouts & Food" },
          ].map((ch) => (
            <button
              key={ch.id}
              onClick={() => {
                realtime.unsubscribe(channel);
                setChannel(ch.id);
              }}
              className={`p-2.5 rounded-xl text-center transition-all ${
                channel === ch.id
                  ? "bg-teal-800 text-white shadow-md font-semibold"
                  : "text-ink-muted hover:text-ink hover:bg-white/40"
              }`}
            >
              <div className="text-xs font-mono">{ch.label}</div>
              <div className="text-[10px] opacity-80 truncate">{ch.desc}</div>
            </button>
          ))}
        </div>

        {/* Mobile Microphone Permission Request Banner */}
        {micPermitted !== true && (
          <div className="mb-6 p-4 rounded-2xl bg-amber-500/10 border border-amber-500/25 flex flex-col sm:flex-row items-center justify-between gap-3 text-center sm:text-left">
            <div className="flex items-center gap-3">
              <div className="w-9 h-9 rounded-full bg-amber-500/20 text-amber-800 flex items-center justify-center shrink-0">
                <Mic className="w-5 h-5" />
              </div>
              <div>
                <h4 className="text-xs font-sans font-bold text-amber-950">Microphone Access Required</h4>
                <p className="text-[11px] text-amber-900/80 font-sans">
                  Tap below to allow microphone access on this phone for push-to-talk.
                </p>
              </div>
            </div>
            <button
              onClick={requestMicPermission}
              className="px-4 py-2 rounded-full bg-teal-800 hover:bg-teal-900 text-white text-xs font-sans font-semibold shrink-0 shadow-md transition active:scale-95"
            >
              Enable Microphone
            </button>
          </div>
        )}

        {/* Live Callout / Speaker Status */}
        <div className="p-4 rounded-2xl bg-white/40 border border-white/30 text-center space-y-1 mb-6">
          <div className="text-xs font-mono uppercase tracking-wider text-ink-muted">
            Callsign: <strong className="text-teal-900">{callsign}</strong>
          </div>
          <div className="text-xs font-sans text-teal-950 font-medium flex items-center justify-center gap-2">
            {isReceiving ? (
              <span className="flex items-center gap-1.5 text-amber-600 animate-pulse font-semibold">
                <Volume2 className="w-4 h-4" />
                Receiving from {activeSpeaker}...
              </span>
            ) : isTalking ? (
              <span className="flex items-center gap-1.5 text-rose-600 animate-pulse font-semibold">
                <Mic className="w-4 h-4" />
                Live on air...
              </span>
            ) : (
              <span>{statusMessage}</span>
            )}
          </div>
        </div>

        {/* Big Push-To-Talk Button */}
        <div className="flex flex-col items-center justify-center py-4">
          <button
            onMouseDown={startTransmitting}
            onMouseUp={stopTransmitting}
            onTouchStart={startTransmitting}
            onTouchEnd={stopTransmitting}
            className={`w-36 h-36 rounded-full flex flex-col items-center justify-center gap-2 shadow-2xl transition-all transform active:scale-95 select-none ${
              isTalking
                ? "bg-rose-600 text-white ring-8 ring-rose-400/40 animate-pulse scale-105"
                : isReceiving
                ? "bg-amber-500 text-white ring-4 ring-amber-300"
                : "bg-gradient-to-tr from-teal-800 to-teal-600 text-white hover:brightness-105"
            }`}
          >
            {isTalking ? (
              <>
                <Mic className="w-10 h-10" />
                <span className="text-xs font-sans font-bold tracking-wider uppercase">TALKING</span>
              </>
            ) : (
              <>
                <Mic className="w-10 h-10" />
                <span className="text-xs font-sans font-bold tracking-wider uppercase">HOLD TO TALK</span>
              </>
            )}
          </button>
          <span className="text-[11px] text-ink-muted font-sans mt-4">
            Press & hold button to speak to connected phones
          </span>
        </div>
      </div>

      {/* Audio Logs / Transmissions */}
      <div className="glass rounded-[28px] p-6 border border-white/40 shadow-sm space-y-4">
        <h3 className="font-serif text-lg text-ink font-normal flex items-center gap-2">
          <Volume2 className="w-4 h-4 text-teal-800" />
          Recent Radio Transmissions
        </h3>

        {transmissions.length === 0 ? (
          <p className="text-xs text-ink-muted text-center py-6">
            No radio voice traffic recorded on this channel yet. Hold the button above to broadcast!
          </p>
        ) : (
          <div className="space-y-2.5 max-h-60 overflow-y-auto pr-1">
            {transmissions.map((t) => (
              <div
                key={t.id}
                className="p-3 rounded-2xl bg-white/40 border border-white/30 flex items-center justify-between text-xs"
              >
                <div className="flex items-center gap-2.5">
                  <div className="w-7 h-7 rounded-full bg-teal-800/10 text-teal-800 flex items-center justify-center">
                    <Radio className="w-3.5 h-3.5" />
                  </div>
                  <div>
                    <span className="font-semibold text-teal-950">{t.sender}</span>
                    <span className="text-ink-muted ml-2 text-[10px]">{t.time}</span>
                  </div>
                </div>

                {t.blobUrl && (
                  <button
                    onClick={() => {
                      if (speakerAudioRef.current) {
                        speakerAudioRef.current.src = t.blobUrl!;
                        speakerAudioRef.current.play();
                      }
                    }}
                    className="px-2.5 py-1 rounded-full bg-teal-800 text-white text-[10px] font-sans font-semibold hover:bg-teal-900 transition"
                  >
                    Replay
                  </button>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
