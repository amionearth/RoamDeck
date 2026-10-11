import React from "react";
import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./lib/auth";
import { RailNav } from "./components/RailNav";
import { BottomNav } from "./components/BottomNav";

// Pages
import { Home } from "./pages/Home";
import { Feed } from "./pages/Feed";
import { TripPage } from "./pages/Trip";
import { TripPoster } from "./pages/TripPoster";
import { Passport } from "./pages/Passport";
import { Friends } from "./pages/Friends";
import { Profile } from "./pages/Profile";
import { Games } from "./pages/Games";
import { GameDetail } from "./pages/GameDetail";
import { Leaderboard } from "./pages/Leaderboard";
import { DevicePage } from "./pages/Device";
import { DemoPage } from "./pages/Demo";
import { GameJoin } from "./pages/GameJoin";
import { RadioPage } from "./pages/Radio";
import { Login } from "./pages/Login";
import { Register } from "./pages/Register";

const queryClient = new QueryClient();

import { AppShell } from "./components/AppShell";

const Layout: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  return <AppShell>{children}</AppShell>;
};

export const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <BrowserRouter>
          <Layout>
            <Routes>
              <Route path="/" element={<Home />} />
              <Route path="/feed" element={<Feed />} />
              <Route path="/trips/:id" element={<TripPage />} />
              <Route path="/trips/:id/poster" element={<TripPoster />} />
              <Route path="/passport" element={<Passport />} />
              <Route path="/friends" element={<Friends />} />
              <Route path="/u/:username" element={<Profile />} />
              <Route path="/games" element={<Games />} />
              <Route path="/games/:id" element={<GameDetail />} />
              <Route path="/leaderboard" element={<Leaderboard />} />
              <Route path="/device" element={<DevicePage />} />
              <Route path="/demo" element={<DemoPage />} />
              <Route path="/radio" element={<RadioPage />} />
              <Route path="/g/:code" element={<GameJoin />} />
              <Route path="/login" element={<Login />} />
              <Route path="/register" element={<Register />} />
            </Routes>
          </Layout>
        </BrowserRouter>
      </AuthProvider>
    </QueryClientProvider>
  );
};

export default App;
