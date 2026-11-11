"use client";

import { useEffect, useRef } from "react";
import { useReducedMotion } from "@/hooks/use-reduced-motion";

/** A soft, lagging light that follows a fine pointer. Disabled on touch. */
export function CursorGlow() {
  const ref = useRef<HTMLDivElement | null>(null);
  const reduced = useReducedMotion();

  useEffect(() => {
    if (reduced) return;
    const node = ref.current;
    if (!node) return;
    if (window.matchMedia("(pointer: coarse)").matches) return;

    let x = window.innerWidth / 2;
    let y = window.innerHeight / 2;
    let tx = x;
    let ty = y;
    let frame = 0;
    let visible = false;

    const move = (event: PointerEvent) => {
      tx = event.clientX;
      ty = event.clientY;
      if (!visible) {
        visible = true;
        node.style.opacity = "1";
      }
    };

    const loop = () => {
      x += (tx - x) * 0.14;
      y += (ty - y) * 0.14;
      node.style.transform = `translate3d(${x - 260}px, ${y - 260}px, 0)`;
      frame = requestAnimationFrame(loop);
    };

    const leave = () => {
      visible = false;
      node.style.opacity = "0";
    };

    window.addEventListener("pointermove", move, { passive: true });
    document.addEventListener("pointerleave", leave);
    frame = requestAnimationFrame(loop);

    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("pointermove", move);
      document.removeEventListener("pointerleave", leave);
    };
  }, [reduced]);

  return (
    <div
      ref={ref}
      aria-hidden="true"
      className="pointer-events-none fixed left-0 top-0 z-0 h-[520px] w-[520px] opacity-0 transition-opacity duration-700"
      style={{
        background:
          "radial-gradient(circle, rgba(200,255,114,0.055) 0%, rgba(110,240,194,0.035) 34%, transparent 68%)",
        mixBlendMode: "screen",
      }}
    />
  );
}
