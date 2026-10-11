import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Search, Moon, Sun, Bell, Radio } from "lucide-react";
import { useAuth } from "../lib/auth";

export const Header: React.FC<{ title?: string; subtitle?: string }> = ({
  title,
  subtitle,
}) => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [searchQuery, setSearchQuery] = useState("");
  const [isDark, setIsDark] = useState(false);

  const toggleDark = () => {
    setIsDark(!isDark);
    document.documentElement.classList.toggle("dark");
  };

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    if (searchQuery.trim()) {
      navigate(`/users/search?q=${encodeURIComponent(searchQuery.trim())}`);
    }
  };

  return (
    <header className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8">
      <div>
        <h1 className="font-serif text-3xl md:text-4xl text-ink font-normal tracking-tight">
          {title || (user ? `Hello, ${user.display_name}` : "FarFromHome")}
        </h1>
        <p className="text-mist text-sm mt-1">
          {subtitle || "Blind-trip exploration companion. Follow the compass, discover the unknown."}
        </p>
      </div>

      <div className="flex items-center space-x-3">
        {/* Pill Search Bar */}
        <form onSubmit={handleSearch} className="relative">
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search explorers or codes..."
            className="w-48 sm:w-64 pl-9 pr-4 py-2 bg-paper rounded-full text-xs border border-mist/20 shadow-sm focus:outline-none focus:ring-2 focus:ring-lagoon transition"
          />
          <Search className="w-4 h-4 text-mist absolute left-3 top-1/2 -translate-y-1/2" />
        </form>

        {/* Theme Toggle */}
        <button
          onClick={toggleDark}
          className="p-2 rounded-full bg-paper border border-mist/20 text-mist hover:text-ink shadow-sm"
          title="Toggle Dark Mode"
        >
          {isDark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </button>

        {/* User Avatar */}
        {user ? (
          <Link
            to={`/u/${user.username}`}
            className="flex items-center space-x-2 p-1 pl-2 rounded-full bg-paper border border-mist/20 hover:border-lagoon transition"
          >
            <span className="text-xs font-medium text-ink hidden sm:inline">{user.display_name}</span>
            <div className="w-8 h-8 rounded-full bg-lagoon text-white font-medium text-xs flex items-center justify-center overflow-hidden">
              {user.avatar_path ? (
                <img src={user.avatar_path} alt={user.display_name} className="w-full h-full object-cover" />
              ) : (
                user.display_name.slice(0, 2).toUpperCase()
              )}
            </div>
          </Link>
        ) : (
          <Link
            to="/login"
            className="px-4 py-2 rounded-full bg-lagoon text-white text-xs font-medium shadow-clay hover:opacity-95 transition"
          >
            Sign in
          </Link>
        )}
      </div>
    </header>
  );
};
