/**
 * camera.js – Wraps Capacitor Camera and falls back to <input type=file> in browsers.
 *
 * Usage:
 *   import { takePhoto } from './camera.js';
 *   const blob = await takePhoto();   // returns a Blob ready to POST
 */

async function getCapacitorCamera() {
  try {
    const { Camera } = await import("https://cdn.jsdelivr.net/npm/@capacitor/camera@6/dist/esm/index.js");
    return Camera;
  } catch {
    return null;
  }
}

/**
 * Opens camera (or file picker in browser). Returns a Blob, or null if cancelled.
 */
export async function takePhoto() {
  const Camera = await getCapacitorCamera();

  if (Camera && window.Capacitor && !window.CapacitorStub) {
    try {
      const photo = await Camera.getPhoto({
        quality: 80,
        allowEditing: false,
        resultType: "base64",   // CameraResultType.Base64
        source: "prompt",       // CameraSource.Prompt  (camera + gallery option)
      });
      // Convert base64 dataURL to Blob
      const dataUrl = `data:image/jpeg;base64,${photo.base64String}`;
      return dataUrlToBlob(dataUrl);
    } catch (e) {
      if (e && e.message && e.message.toLowerCase().includes("cancel")) return null;
      throw e;
    }
  }

  // Browser fallback – file input picker
  return fileInputFallback();
}

function dataUrlToBlob(dataUrl) {
  const [header, base64] = dataUrl.split(",");
  const mime = header.match(/:(.*?);/)[1];
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: mime });
}

function fileInputFallback() {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.capture = "environment"; // hint rear camera
    input.onchange = () => {
      const file = input.files && input.files[0];
      resolve(file || null);
    };
    input.oncancel = () => resolve(null);
    input.click();
  });
}
