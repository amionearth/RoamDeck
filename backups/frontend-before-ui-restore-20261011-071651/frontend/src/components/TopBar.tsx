import React, { useState, useEffect, useRef } from "react";
import { Link, useNavigate, useLocation } from "react-router-dom";
import { Search, ChevronDown, User, Radio, LogOut, Compass } from "lucide-react";
import { useAuth } from "../lib/auth";
import { api } from "../lib/api";

interface TopBarProps {
  title?: string;
  subtitle?: string;
  searchQuery?: string;
  searchPlaceholder?: string;
  onSearchChange?: (q: string) => void;
}

export const TopBar: React.FC<TopBarProps> = ({
  title,
  subtitle,
  searchQuery: externalSearchQuery,
  searchPlaceholder,
  onSearchChange,
}) => {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const isHomeOrFeed = location.pathname === "/" || location.pathname === "/feed";
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<any[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const [showUserMenu, setShowUserMenu] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);
  const userMenuRef = useRef<HTMLDivElement>(null);

  // Close menus on outside click
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
      if (userMenuRef.current && !userMenuRef.current.contains(e.target as Node)) {
        setShowUserMenu(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  // Search users live debounce
  useEffect(() => {
    if (onSearchChange) {
      onSearchChange(searchQuery);
    }
    if (!searchQuery.trim() || !isHomeOrFeed) {
      setSearchResults([]);
      setShowDropdown(false);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        const users = await api.get(`/api/users/search?q=${encodeURIComponent(searchQuery.trim())}`);
        setSearchResults(users || []);
        setShowDropdown(true);
      } catch (e) {
        console.warn("Search error:", e);
      }
    }, 200);
    return () => clearTimeout(timer);
  }, [searchQuery, isHomeOrFeed, onSearchChange]);

  const handleSelectUser = (username: string) => {
    setShowDropdown(false);
    setSearchQuery("");
    navigate(`/u/${username}`);
  };

  // Sync externalSearchQuery if provided
  useEffect(() => {
    if (externalSearchQuery !== undefined) {
      setSearchQuery(externalSearchQuery);
    }
  }, [externalSearchQuery]);

  return (
    <header className="flex flex-col md:flex-row md:items-center justify-between gap-4 mb-8 pt-2">
      {/* Title & Subtitle */}
      <div>
        <h1 className="font-serif text-3xl md:text-4xl text-ink font-normal leading-tight tracking-tight">
          {title || (user ? `Hello, ${user.display_name}` : "FarFromHome")}
        </h1>
        <p className="text-mist text-xs md:text-sm mt-1 max-w-xl">
          {subtitle || "Blind-trip exploration companion. Follow the pointing needle into the unknown."}
        </p>
      </div>

      {/* Right side: Search (Home/Feed only) & User Profile Pill */}
      <div className="flex items-center gap-3">
        {/* Working Search on Home and Feed */}
        {isHomeOrFeed && (
          <div className="relative" ref={dropdownRef}>
            <div className="flex items-center glass-strong rounded-full px-3.5 py-2 w-48 sm:w-64 focus-within:w-72 transition-all">
              <Search className="w-4 h-4 text-mist shrink-0 mr-2" />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={searchPlaceholder || "Search journeys or @user..."}
                className="bg-transparent border-none outline-none text-xs text-ink placeholder:text-mist/70 w-full"
              />
            </div>

            {/* Dropdown of matching users */}
            {showDropdown && searchResults.length > 0 && (
              <div className="absolute right-0 top-full mt-2 w-72 glass-strong rounded-2xl p-2 shadow-2xl z-50 divide-y divide-white/20">
                <span className="text-[10px] font-mono text-mist uppercase px-2 py-1 block">
                  Found Explorers
                </span>
                {searchResults.map((u) => (
                  <button
                    key={u.id}
                    onClick={() => handleSelectUser(u.username)}
                    className="w-full text-left p-2 rounded-xl hover:bg-white/40 flex items-center gap-2.5 transition"
                  >
                    <div className="w-7 h-7 rounded-full bg-lagoon text-white text-[11px] font-semibold flex items-center justify-center shrink-0">
                      {u.display_name.slice(0, 2).toUpperCase()}
                    </div>
                    <div className="truncate">
                      <span className="text-xs font-semibold text-ink block truncate">{u.display_name}</span>
                      <span className="text-[10px] text-mist font-mono">@{u.username} • {u.friend_code}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </div>
        )}

        {/* User Pill & Menu */}
        {user ? (
          <div className="relative" ref={userMenuRef}>
            <button
              onClick={() => setShowUserMenu(!showUserMenu)}
              className="flex items-center gap-2.5 glass-strong rounded-full pl-2 pr-3 py-1.5 hover:bg-white/70 transition shadow-sm"
              title="Explorer Account"
            >
              <div className="w-7 h-7 rounded-full bg-lagoon text-white font-semibold text-xs flex items-center justify-center overflow-hidden shrink-0 shadow-inner">
                {user.avatar_path ? (
                  <img src={user.avatar_path} alt="" className="w-full h-full object-cover" />
                ) : (
                  user.display_name.slice(0, 2).toUpperCase()
                )}
              </div>
              <span className="text-xs font-semibold text-ink max-w-[120px] truncate hidden sm:inline">
                {user.display_name}
              </span>
              <ChevronDown className="w-3.5 h-3.5 text-mist" />
            </button>

            {/* Dropdown Menu */}
            {showUserMenu && (
              <div className="absolute right-0 top-full mt-2 w-48 glass-strong rounded-2xl p-1.5 shadow-2xl z-50">
                <div className="px-3 py-2 border-b border-white/20 mb-1">
                  <span className="text-xs font-bold text-ink block truncate">{user.display_name}</span>
                  <span className="text-[10px] text-mist font-mono">@{user.username}</span>
                </div>
                <Link
                  to={`/u/${user.username}`}
                  onClick={() => setShowUserMenu(false)}
                  className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-semibold text-ink hover:bg-white/50 transition"
                >
                  <User className="w-4 h-4 text-lagoon" /> Profile
                </Link>
                <Link
                  to="/device"
                  onClick={() => setShowUserMenu(false)}
                  className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-semibold text-ink hover:bg-white/50 transition"
                >
                  <Radio className="w-4 h-4 text-signal" /> Pair device
                </Link>
                <button
                  onClick={() => {
                    setShowUserMenu(false);
                    logout();
                    navigate("/login");
                  }}
                  className="w-full flex items-center gap-2 px-3 py-2 rounded-xl text-xs font-semibold text-red-600 hover:bg-red-50/50 transition text-left"
                >
                  <LogOut className="w-4 h-4" /> Log out
                </button>
              </div>
            )}
          </div>
        ) : (
          <Link
            to="/login"
            className="px-5 py-2 rounded-full bg-lagoon text-white text-xs font-semibold shadow-md hover:bg-lagoon/90 transition"
          >
            Sign in
          </Link>
        )}
      </div>
    </header>
  );
};
