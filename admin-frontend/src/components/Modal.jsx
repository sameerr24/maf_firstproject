import { useEffect, useRef } from "react";

/** Accessible dialog: focus moves inside, Escape or the backdrop closes it. */
export default function Modal({ title, onClose, children, wide = false }) {
  const ref = useRef(null);

  useEffect(() => {
    const previous = document.activeElement;
    (ref.current.querySelector("[data-autofocus]") ?? ref.current.querySelector("input, select, textarea, button"))?.focus();
    const onKey = (e) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [onClose]);

  return (
    <div className="backdrop" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} className={`modal${wide ? " modal--wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <div className="modal__head">
          <h2 id="modal-title">{title}</h2>
          <button type="button" className="icon-btn" aria-label="Close" onClick={onClose}>×</button>
        </div>
        {children}
      </div>
    </div>
  );
}
