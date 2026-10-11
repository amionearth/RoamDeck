import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Compass, Sparkles, ArrowRight } from "lucide-react";
import { useAuth } from "../lib/auth";

export const Login: React.FC = () => {
  const { login, demoLogin } = useAuth();
  const navigate = useNavigate();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await login(username, password);
      navigate("/");
    } catch (err: any) {
      setError(err.message || "Invalid credentials");
    } finally {
      setLoading(false);
    }
  };

  const handleDemoLogin = async () => {
    setError("");
    setLoading(true);
    try {
      await demoLogin();
      navigate("/");
    } catch (err: any) {
      setError(err.message || "Demo login failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center p-4 sm:p-6 animate-fade-in">
      <div className="glass rounded-3xl p-8 sm:p-10 max-w-md w-full border border-white/40 shadow-2xl space-y-6 relative overflow-hidden">
        {/* Soft specular sheen */}
        <div className="absolute top-0 right-0 w-48 h-48 bg-white/20 rounded-full blur-2xl pointer-events-none" />

        <div className="text-center relative z-10">
          <div className="w-14 h-14 rounded-2xl bg-teal-800 text-white flex items-center justify-center mx-auto mb-3 shadow-md border border-white/40">
            <Compass className="w-7 h-7" />
          </div>
          <h1 className="font-serif text-3xl sm:text-4xl text-ink font-normal">
            FarFromHome
          </h1>
          <p className="text-xs text-ink-muted font-sans mt-1">
            Blind trip travel companion platform
          </p>
        </div>

        {error && (
          <div className="p-3 rounded-inner bg-rose-500/10 border border-rose-500/30 text-rose-700 text-xs font-sans text-center">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4 relative z-10">
          <div>
            <label className="text-xs font-sans text-teal-950 font-medium block mb-1">
              Username
            </label>
            <input
              type="text"
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="e.g. wanderer"
              className="w-full px-4 py-2.5 rounded-full glass-strong border border-white/40 text-xs font-sans focus:outline-none focus:ring-1 focus:ring-teal-700"
            />
          </div>

          <div>
            <label className="text-xs font-sans text-teal-950 font-medium block mb-1">
              Password
            </label>
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              className="w-full px-4 py-2.5 rounded-full glass-strong border border-white/40 text-xs font-sans focus:outline-none focus:ring-1 focus:ring-teal-700"
            />
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-3 rounded-full bg-teal-800 hover:bg-teal-900 text-white font-sans font-semibold text-xs transition-all shadow-md flex items-center justify-center gap-2 disabled:opacity-50"
          >
            <span>{loading ? "Signing in..." : "Sign in"}</span>
            <ArrowRight className="w-4 h-4" />
          </button>
        </form>

        {/* Demo Fast Login Button */}
        <div className="pt-4 border-t border-white/30 text-center relative z-10 space-y-3">
          <button
            type="button"
            onClick={handleDemoLogin}
            disabled={loading}
            className="w-full py-2.5 rounded-full glass-strong hover:bg-white text-teal-950 font-sans font-medium text-xs transition-all border border-white/50 flex items-center justify-center gap-2 shadow-sm"
          >
            <Sparkles className="w-4 h-4 text-amber-500" />
            <span>Fast Demo Sign In</span>
          </button>

          <p className="text-xs text-ink-muted font-sans">
            Need an account?{" "}
            <Link to="/register" className="text-teal-800 font-semibold hover:underline">
              Create account
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
};
