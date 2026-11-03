import { useCallback, useEffect, useRef, useState } from "react";
import CameraScanner from "./CameraScanner.jsx";

function playTone(success) {
  try {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) return;
    const context = new AudioContextClass();
    const oscillator = context.createOscillator();
    const gain = context.createGain();
    oscillator.frequency.value = success ? 880 : 260;
    gain.gain.value = 0.06;
    oscillator.connect(gain);
    gain.connect(context.destination);
    oscillator.start();
    oscillator.stop(context.currentTime + 0.12);
    oscillator.onended = () => context.close();
  } catch {
    // Audio is optional; scanning must continue if the browser blocks it.
  }
}

function readMuted() {
  try {
    return localStorage.getItem("tamirabot.barcode-scanner-muted") === "true";
  } catch {
    return false;
  }
}

export default function BarcodeScanInput({
  id,
  name = "barcode",
  value = "",
  onChange,
  onScan,
  placeholder = "Scan or type barcode",
  autoFocus = true,
  continuousCamera = false,
  disabled = false,
  flashOnSuccess = false,
}) {
  const inputRef = useRef(null);
  const keyBuffer = useRef("");
  const lastKeyAt = useRef(0);
  const flashTimer = useRef(null);
  const [lastScanned, setLastScanned] = useState("");
  const [scanSuccess, setScanSuccess] = useState(false);
  const [muted, setMuted] = useState(readMuted);

  const submitCode = useCallback(async (rawCode) => {
    const code = String(rawCode || "").trim();
    if (!code) return false;
    setLastScanned(code);
    try {
      const succeeded = await onScan(code);
      setScanSuccess(succeeded !== false);
      if (flashOnSuccess && succeeded !== false) {
        window.clearTimeout(flashTimer.current);
        flashTimer.current = window.setTimeout(() => setScanSuccess(false), 650);
      }
      if (!muted) playTone(succeeded !== false);
      return succeeded !== false;
    } catch {
      setScanSuccess(false);
      if (!muted) playTone(false);
      return false;
    }
  }, [flashOnSuccess, muted, onScan]);
  const handleCameraScan = useCallback(
    (code) => submitCode(code),
    [submitCode],
  );

  useEffect(() => {
    function handleKeyDown(event) {
      const target = event.target;
      const isScanInput = target === inputRef.current;
      const isTextEntry = target instanceof HTMLElement
        && (target.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName));
      if (isTextEntry || event.ctrlKey || event.altKey || event.metaKey) {
        keyBuffer.current = "";
        return;
      }
      if (target !== document.body && !isScanInput) return;
      const now = Date.now();
      if (event.key === "Enter") {
        if (keyBuffer.current.length > 0) {
          event.preventDefault();
          const code = keyBuffer.current;
          keyBuffer.current = "";
          void submitCode(code);
        }
        return;
      }
      if (event.key.length !== 1) return;
      keyBuffer.current = now - lastKeyAt.current <= 50
        ? keyBuffer.current + event.key
        : event.key;
      lastKeyAt.current = now;
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [submitCode]);

  useEffect(() => () => window.clearTimeout(flashTimer.current), []);

  function toggleMuted() {
    setMuted((current) => {
      const next = !current;
      try {
        localStorage.setItem("tamirabot.barcode-scanner-muted", String(next));
      } catch {
        // Keep the in-memory preference if storage is unavailable.
      }
      return next;
    });
  }

  return (
    <div className={`barcode-scan-input${flashOnSuccess && scanSuccess ? " scan-success-flash" : ""}`}>
      <div className="barcode-scan-entry">
        <input
          autoComplete="off"
          autoFocus={autoFocus}
          aria-label="Scan or type barcode"
          disabled={disabled}
          id={id}
          name={name}
          onChange={onChange}
          onKeyDown={(event) => {
            if (event.key === "Enter") {
              event.preventDefault();
              void submitCode(inputRef.current?.value || value);
            }
          }}
          placeholder={placeholder}
          ref={inputRef}
          value={value}
        />
        <button className="catalog-secondary-button barcode-clear-button" disabled={disabled} type="button" onClick={() => {
          onChange?.({ target: { value: "" } });
          inputRef.current?.focus();
        }}>Clear</button>
      </div>
      <CameraScanner
        continuous={continuousCamera}
        disabled={disabled}
        onScan={handleCameraScan}
        buttonLabel="Scan"
        secureMessage="Camera scanning needs a secure (HTTPS) connection. Use a USB scanner or type the code."
        description="Use a camera, USB scanner, or type the code."
      />
      <div className="barcode-scan-meta">
        <span>{lastScanned ? `Last scanned: ${lastScanned}` : "Ready to scan"}</span>
        <button className="catalog-text-link" type="button" onClick={toggleMuted}>
          Sound: {muted ? "Muted" : "On"}
        </button>
      </div>
    </div>
  );
}
