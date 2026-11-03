import { useEffect, useRef, useState } from "react";

export default function CameraScanner({
  onScan,
  continuous = false,
  disabled = false,
  buttonLabel = "Scan with camera",
  secureMessage = "Camera scanning requires HTTPS or localhost. You can still scan with a USB barcode scanner.",
  description = "Camera scanning needs browser camera support and a secure origin (HTTPS).",
}) {
  const videoRef = useRef(null);
  const recentScansRef = useRef(new Map());
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!scanning || disabled) {
      if (disabled && scanning) setScanning(false);
      return undefined;
    }
    let cancelled = false;
    let controls;
    let reader;
    if (!window.isSecureContext) {
      setError(secureMessage);
      setScanning(false);
      return undefined;
    }
    async function startCamera() {
      try {
        const { BrowserMultiFormatReader } = await import("@zxing/browser");
        if (cancelled) return;
        reader = new BrowserMultiFormatReader();
        controls = await reader.decodeFromVideoDevice(undefined, videoRef.current, (result) => {
          if (cancelled || !result) return;
          const code = result.getText();
          const now = Date.now();
          if (continuous) {
            const lastSeenAt = recentScansRef.current.get(code);
            if (lastSeenAt && now - lastSeenAt < 1500) return;
            recentScansRef.current.set(code, now);
            recentScansRef.current.forEach((seenAt, seenCode) => {
              if (now - seenAt >= 1500) recentScansRef.current.delete(seenCode);
            });
          }
          if (!continuous) setScanning(false);
          onScan(code);
        });
        if (cancelled) controls.stop();
        else setError("");
      } catch (failure) {
        if (cancelled) return;
        setError(failure.name === "NotAllowedError"
          ? "Camera permission was denied. Allow camera access or use a USB barcode scanner."
          : "Could not start the camera. Use a USB barcode scanner instead.");
        setScanning(false);
      }
    }
    startCamera();

    return () => {
      cancelled = true;
      controls?.stop();
      reader?.reset();
    };
  }, [continuous, disabled, onScan, scanning, secureMessage]);

  return (
    <div className="scanner-camera">
      <div className="scanner-actions">
        <button className="catalog-secondary-button" disabled={disabled} type="button" onClick={() => { setError(""); setScanning((value) => !value); }}>
          {scanning ? "Stop camera" : buttonLabel}
        </button>
        <span>{description}</span>
      </div>
      {scanning && <video className="scanner-video" ref={videoRef} muted playsInline />}
      {error && <div className="catalog-alert" role="alert">{error}</div>}
    </div>
  );
}
