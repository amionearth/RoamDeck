import React, { useEffect, useState } from "react";
import { Radio, Battery, Wifi, Trash2, Check, AlertCircle } from "lucide-react";
import { Header } from "../components/Header";
import { api } from "../lib/api";

export const DevicePage: React.FC = () => {
  const [devices, setDevices] = useState<any[]>([]);
  const [pairingCode, setPairingCode] = useState("");
  const [statusMessage, setStatusMessage] = useState("");
  const [isLoading, setIsLoading] = useState(true);

  const fetchDevices = async () => {
    try {
      const data = await api.get("/api/devices");
      setDevices(data);
    } catch (err) {
      console.error(err);
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchDevices();
  }, []);

  const handlePair = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!pairingCode.trim()) return;
    try {
      const res = await api.post("/api/devices/pair", { code: pairingCode.trim() });
      setStatusMessage(`Successfully paired with ${res.device_name}!`);
      setPairingCode("");
      fetchDevices();
      setTimeout(() => setStatusMessage(""), 4000);
    } catch (err: any) {
      setStatusMessage(err.message || "Invalid or expired pairing code");
    }
  };

  const handleUnpair = async (id: string) => {
    try {
      await api.delete(`/api/devices/${id}`);
      fetchDevices();
    } catch (err) {
      console.error(err);
    }
  };

  return (
    <div className="max-w-4xl mx-auto px-4 sm:px-6 py-6 pb-24 lg:pb-12 space-y-8">
      <Header
        title="Hardware Devices"
        subtitle="Pair and manage your Raspberry Pi 5 handheld blind trip devices."
      />

      {/* Pairing Form Card */}
      <div className="bg-paper rounded-card p-6 md:p-8 shadow-clay border border-mist/20">
        <h3 className="font-serif text-2xl text-ink font-normal mb-2">Pair a New Device</h3>
        <p className="text-xs text-mist mb-6 max-w-lg">
          Power on your Raspberry Pi 5. When the device screen displays a 6-character pairing code, enter it below.
        </p>

        <form onSubmit={handlePair} className="flex flex-col sm:flex-row gap-3 max-w-md">
          <input
            type="text"
            maxLength={10}
            value={pairingCode}
            onChange={(e) => setPairingCode(e.target.value.toUpperCase())}
            placeholder="e.g. 7X9K42"
            className="flex-1 px-4 py-3 bg-fog rounded-full font-mono text-center tracking-widest uppercase text-base border border-mist/30 focus:outline-none focus:ring-2 focus:ring-lagoon"
          />
          <button
            type="submit"
            disabled={!pairingCode.trim()}
            className="px-6 py-3 rounded-full bg-lagoon text-white font-semibold text-xs shadow-clay hover:opacity-95 disabled:opacity-50"
          >
            Pair device
          </button>
        </form>

        {statusMessage && (
          <div className="mt-4 p-3 rounded-tile bg-lagoon/10 border border-lagoon/20 text-lagoon text-xs font-semibold">
            {statusMessage}
          </div>
        )}
      </div>

      {/* Paired Devices List */}
      <div className="bg-paper rounded-card p-6 md:p-8 shadow-clay border border-mist/20">
        <h3 className="font-serif text-2xl text-ink font-normal mb-4">
          Paired Devices ({devices.length})
        </h3>

        {devices.length === 0 ? (
          <p className="text-sm text-mist text-center py-8">
            No devices paired to your account yet. Use the form above to pair your Pi.
          </p>
        ) : (
          <div className="space-y-4">
            {devices.map((d) => (
              <div
                key={d.id}
                className="p-5 rounded-tile bg-fog/40 border border-mist/20 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4"
              >
                <div className="flex items-center gap-4">
                  <div className="w-12 h-12 rounded-tile bg-lagoon/10 text-lagoon flex items-center justify-center shrink-0">
                    <Radio className="w-6 h-6" />
                  </div>
                  <div>
                    <h4 className="font-serif text-lg font-semibold text-ink">{d.name}</h4>
                    <div className="flex items-center gap-4 text-xs text-mist mt-1">
                      <span className="flex items-center gap-1 font-mono">
                        <Battery className="w-3.5 h-3.5 text-lagoon" /> {d.battery_pct}% Battery
                      </span>
                      <span className="flex items-center gap-1 font-semibold">
                        <Wifi className="w-3.5 h-3.5 text-signal" /> {d.online ? "Online" : "Offline"}
                      </span>
                      <span>Last seen: {d.last_seen_at ? new Date(d.last_seen_at).toLocaleTimeString() : "Recent"}</span>
                    </div>
                  </div>
                </div>

                <button
                  onClick={() => handleUnpair(d.id)}
                  className="px-4 py-2 rounded-full border border-red-200 text-red-600 hover:bg-red-50 text-xs font-semibold flex items-center gap-1.5 transition self-end sm:self-auto"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                  <span>Remove device</span>
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
