import QRCode from "qrcode";

export interface StampOptions {
  orientation: "portrait" | "landscape";
  destination: string;
  distance_m: number;
  duration_minutes?: number;
  rating?: number | null;
  dateStr?: string;
  subtitle?: string;
  imageUrl?: string;
  tripUrl: string;
  rarity: "common" | "rare" | "epic" | "legendary" | string;
  serial: string;
}

const RARITY_ACCENT_COLORS: Record<string, string> = {
  common: "#B8C7CC",
  rare: "#8DC9DF",
  epic: "#B79BE0",
  legendary: "#DFB96E",
};

// Helper to convert an image URL (or local path) to a base64 Data URL
async function imageUrlToDataUrl(url: string): Promise<string> {
  try {
    const res = await fetch(url);
    const blob = await res.blob();
    return new Promise((resolve) => {
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result as string);
      reader.onerror = () => resolve("");
      reader.readAsDataURL(blob);
    });
  } catch (e) {
    console.warn("Failed to convert image to data URL, using fallback", e);
    // 1x1 transparent GIF fallback
    return "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7";
  }
}

export async function generateStampSvg(options: StampOptions): Promise<string> {
  const isPortrait = options.orientation === "portrait";
  const templatePath = isPortrait
    ? "/ticket-studio/travel-ticket-portrait.svg"
    : "/ticket-studio/travel-ticket-landscape.svg";

  const templateResp = await fetch(templatePath);
  if (!templateResp.ok) {
    throw new Error(`Failed to load stamp template: ${templatePath}`);
  }
  const svgText = await templateResp.text();

  // Parse SVG using DOMParser (Section 6.2 fix: avoid broken regex!)
  const doc = new DOMParser().parseFromString(svgText, "image/svg+xml");

  const setText = (id: string, text: string) => {
    const el = doc.querySelector(`#${id}`);
    if (el) el.textContent = text;
  };

  // 1. Destination with dynamic font size for long text
  const destName = (options.destination || "MYSTERY DESTINATION").toUpperCase();
  const destEl = doc.querySelector("#destination") as SVGTextElement | null;
  if (destEl) {
    destEl.textContent = destName;
    const origSize = isPortrait ? 28 : 34;
    const threshold = isPortrait ? 14 : 20;
    if (destName.length > threshold) {
      const newSize = Math.max(14, Math.round((origSize * threshold) / destName.length));
      destEl.setAttribute("font-size", `${newSize}`);
    }
  }

  // 2. Distance
  const distKm = (options.distance_m ? options.distance_m / 1000 : 8.2).toFixed(1);
  setText("distance", `DISTANCE · ${distKm} KM`);

  // 3. Duration
  const mins = options.duration_minutes || Math.round((options.distance_m || 5000) / 80);
  let durText = `TIME · ${mins} MIN`;
  if (mins >= 60) {
    const hrs = Math.floor(mins / 60);
    const rem = mins % 60;
    durText = rem > 0 ? `TIME · ${hrs} HR ${rem} MIN` : `TIME · ${hrs} HR`;
  }
  setText("duration", durText);

  // 4. Rating
  let ratingText = "RATING · NOT RATED";
  if (options.rating && options.rating > 0) {
    const fullStars = Math.min(5, Math.max(1, Math.round(options.rating)));
    const stars = "★".repeat(fullStars) + "☆".repeat(5 - fullStars);
    ratingText = `RATING · ${stars} ${fullStars}/5`;
  }
  setText("rating", ratingText);

  // 5. Date
  const dateObj = options.dateStr ? new Date(options.dateStr) : new Date();
  if (isPortrait) {
    const dayStr = String(dateObj.getDate()).padStart(2, "0");
    const yearStr = String(dateObj.getFullYear());
    setText("ticket-date", dayStr);
    setText("ticket-year", yearStr);
  } else {
    const months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
    const formatted = `${String(dateObj.getDate()).padStart(2, "0")} ${months[dateObj.getMonth()]} ${dateObj.getFullYear()}`;
    setText("ticket-date", formatted);
  }

  // 6. Subtitle & Tagline
  const moodSub = options.subtitle || "A LITTLE WAY OFF THE MAP";
  setText("subtitle", moodSub.toUpperCase());

  // 7. Stamp Label & Serial
  const rarityUpper = (options.rarity || "COMMON").toUpperCase();
  setText("ticket-label", `${rarityUpper} STAMP`);
  setText("serial", options.serial || "FFH-STAMP-CERT");

  // 8. Portrait Accent Panel color
  const accentColor = RARITY_ACCENT_COLORS[options.rarity.toLowerCase()] || RARITY_ACCENT_COLORS.common;
  const accentPanel = doc.querySelector("#accent-panel");
  if (accentPanel) {
    accentPanel.setAttribute("fill", accentColor);
  }

  // 9. Embed Destination Photo as Data URL
  if (options.imageUrl) {
    const dataUrl = await imageUrlToDataUrl(options.imageUrl);
    const placeImg = doc.querySelector("#place-image");
    if (placeImg && dataUrl) {
      placeImg.setAttribute("href", dataUrl);
    }
  }

  // 10. Generate Local QR Code with qrcode npm package (Section 6.3)
  try {
    const qrDataUrl = await QRCode.toDataURL(options.tripUrl || window.location.href, {
      margin: 0,
      width: 220,
      color: {
        dark: "#171717",
        light: "#FFFFFF",
      },
    });

    const qrSlot = doc.querySelector("#qr-slot");
    if (qrSlot) {
      // Find dimensions of existing rect inside qr-slot
      const rect = qrSlot.querySelector("rect");
      const x = rect?.getAttribute("x") || (isPortrait ? "244" : "772");
      const y = rect?.getAttribute("y") || (isPortrait ? "548" : "290");
      const w = rect?.getAttribute("width") || (isPortrait ? "68" : "72");
      const h = rect?.getAttribute("height") || (isPortrait ? "68" : "72");

      qrSlot.innerHTML = `<image x="${x}" y="${y}" width="${w}" height="${h}" href="${qrDataUrl}" />`;
    }
  } catch (err) {
    console.warn("Local QR generation failed:", err);
  }

  return new XMLSerializer().serializeToString(doc.documentElement);
}

export async function exportStampAsPng(svgString: string, scale = 3): Promise<string> {
  return exportStampPng(svgString, scale);
}

export async function exportStampPng(svgString: string, scale = 3): Promise<string> {
  return new Promise((resolve, reject) => {
    const blob = new Blob([svgString], { type: "image/svg+xml;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const img = new Image();

    img.onload = () => {
      const canvas = document.createElement("canvas");
      canvas.width = img.width * scale;
      canvas.height = img.height * scale;
      const ctx = canvas.getContext("2d");
      if (!ctx) {
        URL.revokeObjectURL(url);
        reject(new Error("Canvas context unavailable"));
        return;
      }
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      URL.revokeObjectURL(url);
      resolve(canvas.toDataURL("image/png"));
    };

    img.onerror = (e) => {
      URL.revokeObjectURL(url);
      reject(e);
    };

    img.src = url;
  });
}

export function downloadFile(content: string, filename: string, type: "svg" | "png") {
  const link = document.createElement("a");
  link.download = filename;
  if (type === "png") {
    link.href = content;
  } else {
    const blob = new Blob([content], { type: "image/svg+xml;charset=utf-8" });
    link.href = URL.createObjectURL(blob);
  }
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
}
