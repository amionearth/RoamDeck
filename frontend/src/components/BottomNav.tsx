import React, { useState } from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { Compass, Sparkles, Award, User, QrCode } from "lucide-react";
import { useAuth } from "../lib/auth";

export const BottomNav: React.FC = () => {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [showJoinModal, setShowJoinModal] = useState(false);
  const [gameCode, setGameCode] = useState("");

  const handleJoinGame = (e: React.FormEvent) => {
    e.preventDefault();
    if (gameCode.trim()) {
      setShowJoinModal(false);
      navigate(`/g/${gameCode.trim().toUpperCase()}`);
    }
  };

  return (
    <>
      <div className="lg:hidden fixed bottom-4 left-4 right-4 z-40 flex justify-center">
        <nav className="flex items-center justify-between bg-paper/95 backdrop-blur-md px-5 py-2.5 rounded-full shadow-clay border border-mist/20 w-full max-w-md">
          <NavLink
            to="/"
            className={({ isActive }) =>
              `flex flex-col items-center py-1 transition ${isActive ? "text-lagoon font-medium" : "text-mist"}`
            }
          >
            <Compass className="w-5 h-5" />
            <span className="text-[10px] mt-0.5">Home</span>
          </NavLink>

          <NavLink
            to="/feed"
            className={({ isActive }) =>
              `flex flex-col items-center py-1 transition ${isActive ? "text-lagoon font-medium" : "text-mist"}`
            }
          >
            <Sparkles className="w-5 h-5" />
            <span className="text-[10px] mt-0.5">Feed</span>
          </NavLink>

          {/* Round Center Button "Join game" */}
          <button
            onClick={() => setShowJoinModal(true)}
            className="flex items-center justify-center w-12 h-12 -mt-5 rounded-full bg-lagoon text-white shadow-clay transform active:scale-95 transition"
            title="Join game with code"
          >
            <QrCode className="w-6 h-6" />
          </button>

          <NavLink
            to="/passport"
            className={({ isActive }) =>
              `flex flex-col items-center py-1 transition ${isActive ? "text-lagoon font-medium" : "text-mist"}`
            }
          >
            <Award className="w-5 h-5" />
            <span className="text-[10px] mt-0.5">Passport</span>
          </NavLink>

          <NavLink
            to={user ? `/u/${user.username}` : "/login"}
            className={({ isActive }) =>
              `flex flex-col items-center py-1 transition ${isActive ? "text-lagoon font-medium" : "text-mist"}`
            }
          >
            <User className="w-5 h-5" />
            <span className="text-[10px] mt-0.5">Profile</span>
          </NavLink>
        </nav>
      </div>

      {/* Join Game Modal */}
      {showJoinModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="bg-paper p-6 rounded-card max-w-sm w-full shadow-clay border border-mist/20">
            <h3 className="font-serif text-2xl text-ink mb-2">Join a Game</h3>
            <p className="text-sm text-mist mb-4">Enter the 5-character code from the host device.</p>
            <form onSubmit={handleJoinGame} className="space-y-4">
              <input
                type="text"
                maxLength={8}
                value={gameCode}
                onChange={(e) => setGameCode(e.target.value.toUpperCase())}
                placeholder="e.g. KOZ42"
                className="w-full px-4 py-3 text-center text-xl font-mono tracking-widest uppercase bg-fog rounded-tile border border-mist/30 focus:outline-none focus:ring-2 focus:ring-lagoon"
                autoFocus
              />
              <div className="flex space-x-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowJoinModal(false)}
                  className="flex-1 py-2.5 rounded-full border border-mist/30 text-sm font-medium text-mist hover:text-ink"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={!gameCode.trim()}
                  className="flex-1 py-2.5 rounded-full bg-lagoon text-white text-sm font-medium shadow-sm disabled:opacity-50"
                >
                  Join game
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </>
  );
};
