"use client";

import { useCallback, useRef } from "react";

interface TiltOptions {
  max?: number;
  scale?: number;
}

/**
 * Pointer tilt without React state: writes CSS custom properties on the element
 * inside requestAnimationFrame so a 120 fps compositor stays free of re-renders.
 */
export function useTilt<T extends HTMLElement>(options: TiltOptions = {}) {
  const ref = useRef<T | null>(null);
  const frame = useRef<number | null>(null);
  const { max = 7, scale = 1.01 } = options;

  const onPointerMove = useCallback(
    (event: React.PointerEvent<T>) => {
      const node = ref.current;
      if (!node || event.pointerType === "touch") return;
      const rect = node.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width;
      const y = (event.clientY - rect.top) / rect.height;
      if (frame.current) cancelAnimationFrame(frame.current);
      frame.current = requestAnimationFrame(() => {
        node.style.setProperty("--tilt-x", `${(0.5 - y) * max}deg`);
        node.style.setProperty("--tilt-y", `${(x - 0.5) * max}deg`);
        node.style.setProperty("--glow-x", `${x * 100}%`);
        node.style.setProperty("--glow-y", `${y * 100}%`);
        node.style.setProperty("--tilt-scale", `${scale}`);
      });
    },
    [max, scale],
  );

  const onPointerLeave = useCallback(() => {
    const node = ref.current;
    if (!node) return;
    if (frame.current) cancelAnimationFrame(frame.current);
    node.style.setProperty("--tilt-x", "0deg");
    node.style.setProperty("--tilt-y", "0deg");
    node.style.setProperty("--tilt-scale", "1");
  }, []);

  return { ref, onPointerMove, onPointerLeave };
}
