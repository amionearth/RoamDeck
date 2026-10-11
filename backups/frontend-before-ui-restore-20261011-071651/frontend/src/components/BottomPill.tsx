import React, { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { Compass, Sparkles, Award, User, QrCode, Users, Radio } from "lucide-react";
import { useAuth } from "../lib/auth";

export const BottomPill: React.FC = () => {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [showJoinModal, setShowJoinModal] = useState(false);
  const [code, setCode] = useState("");

  const handleJoin = (e: React.FormEvent) => {
    e.preventDefault();
    if (code.trim()) {
      setShowJoinModal(false);
      navigate(`/g/${code.trim().toUpperCase()}`);
    }
  };

  return (
    <>
      <div className="md:hidden fixed bottom-3 left-2 right-2 sm:left-4 sm:right-4 z-40 flex justify-center pb-[env(safe-area-inset-bottom)]">
        <nav className="flex items-center justify-around glass-strong px-2 sm:px-4 py-1.5 rounded-full shadow-2xl w-full max-w-md border border-white/40">
          <NavLink
            to="/"
            className={({ isActive }) =>
              `flex flex-col items-center py-0.5 px-1.5 transition ${
                isActive ? "text-lagoon font-semibold" : "text-ink/60"
              }`
            }
          >
            <Compass className="w-4 h-4 sm:w-5 sm:h-5 stroke-[1.8]" />
            <span className="text-[9px] sm:text-[10px] mt-0.5">Home</span>
          </NavLink>

          <NavLink
            to="/feed"
            className={({ isActive }) =>
              `flex flex-col items-center py-0.5 px-1.5 transition ${
                isActive ? "text-lagoon font-semibold" : "text-ink/60"
              }`
            }
          >
            <Sparkles className="w-4 h-4 sm:w-5 sm:h-5 stroke-[1.8]" />
            <span className="text-[9px] sm:text-[10px] mt-0.5">Feed</span>
          </NavLink>

          <NavLink
            to="/passport"
            className={({ isActive }) =>
              `flex flex-col items-center py-0.5 px-1.5 transition ${
                isActive ? "text-lagoon font-semibold" : "text-ink/60"
              }`
            }
          >
            <Award className="w-4 h-4 sm:w-5 sm:h-5 stroke-[1.8]" />
            <span className="text-[9px] sm:text-[10px] mt-0.5">Stamps</span>
          </NavLink>

          {/* Large Round Centre Button: Join Game */}
          <button
            onClick={() => setShowJoinModal(true)}
            className="flex items-center justify-center w-11 h-11 sm:w-12 sm:h-12 -mt-4 rounded-full bg-lagoon text-white shadow-xl hover:bg-lagoon/90 active:scale-95 transition shrink-0"
            title="Join game"
          >
            <QrCode className="w-5 h-5 sm:w-6 sm:h-6 stroke-[1.8]" />
          </button>

          <NavLink
            to="/radio"
            className={({ isActive }) =>
              `flex flex-col items-center py-0.5 px-1.5 transition ${
                isActive ? "text-lagoon font-semibold" : "text-ink/60"
              }`
            }
          >
            <Radio className="w-4 h-4 sm:w-5 sm:h-5 stroke-[1.8]" />
            <span className="text-[9px] sm:text-[10px] mt-0.5">Radio</span>
          </NavLink>

          <NavLink
            to="/friends"
            className={({ isActive }) =>
              `flex flex-col items-center py-0.5 px-1.5 transition ${
                isActive ? "text-lagoon font-semibold" : "text-ink/60"
              }`
            }
          >
            <Users className="w-4 h-4 sm:w-5 sm:h-5 stroke-[1.8]" />
            <span className="text-[9px] sm:text-[10px] mt-0.5">Friends</span>
          </NavLink>

          <NavLink
            to={user ? `/u/${user.username}` : "/login"}
            className={({ isActive }) =>
              `flex flex-col items-center py-0.5 px-1.5 transition ${
                isActive ? "text-lagoon font-semibold" : "text-ink/60"
              }`
            }
          >
            <User className="w-4 h-4 sm:w-5 sm:h-5 stroke-[1.8]" />
            <span className="text-[9px] sm:text-[10px] mt-0.5">Profile</span>
          </NavLink>
        </nav>
      </div>

      {showJoinModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="glass-strong p-6 rounded-[28px] max-w-xs w-full shadow-2xl">
            <h3 className="font-serif text-2xl text-ink mb-1">Join Game</h3>
            <p className="text-xs text-mist mb-4">Enter the code shown on the host device.</p>
            <form onSubmit={handleJoin} className="space-y-4">
              <input
                type="text"
                value={code}
                maxLength={8}
                onChange={(e) => setCode(e.target.value.toUpperCase())}
                placeholder="e.g. KOZ88"
                className="w-full px-4 py-3 bg-white/70 rounded-2xl text-center text-lg font-mono font-bold tracking-widest uppercase border border-white/60 focus:outline-none focus:ring-2 focus:ring-lagoon"
                autoFocus
              />
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setShowJoinModal(false)}
                  className="flex-1 py-2.5 rounded-full bg-white/50 text-xs font-semibold text-ink"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!code.trim()}
                  className="flex-1 py-2.5 rounded-full bg-lagoon text-white text-xs font-semibold shadow-md disabled:opacity-50"
                >
                  Enter
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
};
