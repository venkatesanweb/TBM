import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

const guards = [];
let handlersRegistered = false;
let allowNextPopstate = false;

function snapshotValue(value) {
  if (typeof File !== "undefined" && value instanceof File) {
    return value.size ? { name: value.name, size: value.size, lastModified: value.lastModified } : null;
  }
  if (Array.isArray(value)) return value.map(snapshotValue);
  if (value && typeof value === "object") {
    return Object.fromEntries(Object.keys(value).sort().map((key) => [key, snapshotValue(value[key])]));
  }
  return value;
}

export function readFormValues(form) {
  if (!form) return {};
  return Object.fromEntries([...form.elements]
    .filter((element) => element.name && element.type !== "submit" && element.type !== "button")
    .map((element) => [
      element.name,
      element.type === "checkbox" || element.type === "radio"
        ? element.checked
        : element.type === "file"
          ? element.files?.[0] || null
          : element.value,
    ]));
}

function activeGuard() {
  return [...guards].reverse().find((guard) => guard.dirty);
}

function registerHandlers() {
  if (handlersRegistered) return;
  handlersRegistered = true;

  document.addEventListener("click", (event) => {
    const guard = activeGuard();
    const link = event.target.closest?.("a[href]");
    if (!guard || !link || event.defaultPrevented || event.button !== 0
      || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey
      || link.target || link.hasAttribute("download")) return;
    const target = new URL(link.href, window.location.href);
    if (target.origin !== window.location.origin) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    guard.requestNavigation(`${target.pathname}${target.search}${target.hash}`);
  }, true);

  window.addEventListener("popstate", (event) => {
    if (allowNextPopstate) {
      allowNextPopstate = false;
      return;
    }
    const guard = activeGuard();
    if (!guard) return;
    event.stopImmediatePropagation();
    window.history.pushState({}, "", guard.currentHref);
    guard.requestBack();
  });

  document.addEventListener("keydown", (event) => {
    const guard = activeGuard();
    if (!guard || event.key !== "Escape" || event.target.closest?.('[role="dialog"]')) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    guard.requestClose();
  }, true);
}

if (typeof document !== "undefined") registerHandlers();

export function useUnsavedChanges(formRef, getValues = () => readFormValues(formRef.current), onClose) {
  const navigate = useNavigate();
  const location = useLocation();
  const [dirty, setDirty] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const initialSnapshot = useRef(null);
  const pendingNavigation = useRef(null);
  const guardRef = useRef(null);
  const readValuesRef = useRef(getValues);
  const onCloseRef = useRef(onClose);
  const navigateRef = useRef(navigate);
  const locationRef = useRef(location);

  readValuesRef.current = getValues;
  onCloseRef.current = onClose;
  navigateRef.current = navigate;
  locationRef.current = location;

  const updateDirty = useCallback(() => {
    if (!initialSnapshot.current) return;
    const current = JSON.stringify(snapshotValue(readValuesRef.current()));
    const nextDirty = current !== initialSnapshot.current;
    setDirty(nextDirty);
    if (guardRef.current) guardRef.current.dirty = nextDirty;
  }, []);

  useLayoutEffect(() => {
    initialSnapshot.current = JSON.stringify(snapshotValue(readValuesRef.current()));
    const guard = {
      dirty: false,
      currentHref: `${locationRef.current.pathname}${locationRef.current.search}${locationRef.current.hash}`,
      requestClose: () => {
        if (guard.dirty) setConfirming(true);
        else onCloseRef.current?.();
      },
      requestNavigation: (destination) => {
        pendingNavigation.current = { destination };
        setConfirming(true);
      },
      requestBack: () => {
        pendingNavigation.current = { back: true };
        setConfirming(true);
      },
    };
    guardRef.current = guard;
    guards.push(guard);
    return () => {
      const index = guards.indexOf(guard);
      if (index >= 0) guards.splice(index, 1);
    };
  }, []);

  useEffect(() => {
    if (guardRef.current) {
      guardRef.current.currentHref = `${location.pathname}${location.search}${location.hash}`;
    }
  }, [location.pathname, location.search, location.hash]);

  useEffect(() => {
    if (!dirty) return undefined;
    const beforeUnload = (event) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", beforeUnload);
    return () => window.removeEventListener("beforeunload", beforeUnload);
  }, [dirty]);

  const requestClose = useCallback(() => {
    if (dirty) setConfirming(true);
    else onCloseRef.current?.();
  }, [dirty]);

  const discard = useCallback(() => {
    setConfirming(false);
    const pending = pendingNavigation.current;
    pendingNavigation.current = null;
    if (pending?.destination) navigateRef.current(pending.destination);
    else if (pending?.back) {
      allowNextPopstate = true;
      window.history.back();
    }
    else onCloseRef.current?.();
  }, []);

  const keepEditing = useCallback(() => {
    pendingNavigation.current = null;
    setConfirming(false);
  }, []);

  const saveFromDialog = useCallback((save) => {
    setConfirming(false);
    window.requestAnimationFrame(() => {
      if (save) save();
      else formRef.current?.requestSubmit();
    });
  }, [formRef]);

  return {
    dirty,
    confirming,
    updateDirty,
    requestClose,
    discard,
    keepEditing,
    saveFromDialog,
  };
}

export function UnsavedChangesDialog({ open, onSave, onDiscard, onKeepEditing }) {
  const dialogRef = useRef(null);
  const keepRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const previouslyFocused = document.activeElement;
    keepRef.current?.focus();
    const handleKeyDown = (event) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onKeepEditing();
      } else if (event.key === "Tab") {
        const buttons = dialogRef.current?.querySelectorAll("button:not([disabled])");
        if (!buttons?.length) return;
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus?.();
    };
  }, [open, onKeepEditing]);

  if (!open) return null;
  return (
    <div className="unsaved-dialog-backdrop">
      <section aria-labelledby="unsaved-dialog-title" aria-modal="true" className="unsaved-dialog" ref={dialogRef} role="dialog">
        <h2 id="unsaved-dialog-title">Unsaved changes</h2>
        <p>You have changes that are not saved. What would you like to do?</p>
        <div className="unsaved-dialog-actions">
          <button className="catalog-primary-button" onClick={onSave} type="button">Save changes</button>
          <button className="catalog-danger-button" onClick={onDiscard} type="button">Discard changes</button>
          <button className="catalog-secondary-button" onClick={onKeepEditing} ref={keepRef} type="button">Keep editing</button>
        </div>
      </section>
    </div>
  );
}
