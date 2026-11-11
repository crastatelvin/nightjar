"use client";

import { useEffect, useRef } from "react";
import { useReducedMotion } from "@/hooks/use-reduced-motion";

interface Particle {
  x: number;
  y: number;
  vx: number;
  vy: number;
  r: number;
  depth: number;
  tint: [number, number, number];
}

const TINTS: Array<[number, number, number]> = [
  [200, 255, 114],
  [110, 240, 194],
  [124, 196, 255],
  [185, 160, 255],
];

/**
 * Ambient dust field. DPR-aware, capped particle count, pointer parallax and
 * additive glow. Pauses when the tab is hidden; renders one static frame when
 * reduced motion is requested.
 */
export function AmbientCanvas() {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const reduced = useReducedMotion();
  const pointer = useRef({ x: 0.5, y: 0.4, tx: 0.5, ty: 0.4 });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d", { alpha: true });
    if (!ctx) return;

    let width = 0;
    let height = 0;
    let dpr = 1;
    let particles: Particle[] = [];
    let frame = 0;
    let running = true;
    let last = performance.now();

    const build = () => {
      const area = width * height;
      const count = Math.max(22, Math.min(78, Math.round(area / 24000)));
      particles = Array.from({ length: count }, () => {
        const depth = 0.35 + Math.random() * 0.65;
        return {
          x: Math.random() * width,
          y: Math.random() * height,
          vx: (Math.random() - 0.5) * 0.12,
          vy: (Math.random() - 0.5) * 0.1,
          r: 0.6 + Math.random() * 1.5 * depth,
          depth,
          tint: TINTS[Math.floor(Math.random() * TINTS.length)],
        };
      });
    };

    const resize = () => {
      dpr = Math.min(window.devicePixelRatio || 1, 1.8);
      width = window.innerWidth;
      height = window.innerHeight;
      canvas.width = Math.round(width * dpr);
      canvas.height = Math.round(height * dpr);
      canvas.style.width = `${width}px`;
      canvas.style.height = `${height}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      build();
      if (reduced) draw(0);
    };

    const draw = (delta: number) => {
      ctx.clearRect(0, 0, width, height);
      pointer.current.x += (pointer.current.tx - pointer.current.x) * 0.045;
      pointer.current.y += (pointer.current.ty - pointer.current.y) * 0.045;
      const px = pointer.current.x * width;
      const py = pointer.current.y * height;

      ctx.globalCompositeOperation = "lighter";
      for (const particle of particles) {
        if (!reduced && delta) {
          particle.x += particle.vx * delta * 0.06 + (px - width / 2) * 0.000015 * particle.depth;
          particle.y += particle.vy * delta * 0.06 + (py - height / 2) * 0.000015 * particle.depth;
          if (particle.x < -20) particle.x = width + 20;
          if (particle.x > width + 20) particle.x = -20;
          if (particle.y < -20) particle.y = height + 20;
          if (particle.y > height + 20) particle.y = -20;
        }
        const dx = particle.x - px;
        const dy = particle.y - py;
        const distance = Math.hypot(dx, dy);
        const near = Math.max(0, 1 - distance / 220);
        const radius = particle.r + near * 1.3;
        const [r, g, b] = particle.tint;
        const alpha = 0.1 + particle.depth * 0.16 + near * 0.35;
        ctx.beginPath();
        ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
        ctx.arc(particle.x, particle.y, radius, 0, Math.PI * 2);
        ctx.fill();
      }

      // A restrained constellation near the pointer, capped for fill rate.
      if (!reduced) {
        for (let i = 0; i < particles.length; i += 1) {
          const a = particles[i];
          const da = Math.hypot(a.x - px, a.y - py);
          if (da > 170) continue;
          for (let j = i + 1; j < particles.length; j += 1) {
            const b = particles[j];
            const db = Math.hypot(b.x - px, b.y - py);
            if (db > 170) continue;
            const d = Math.hypot(a.x - b.x, a.y - b.y);
            if (d > 92) continue;
            ctx.beginPath();
            ctx.strokeStyle = `rgba(160, 220, 210, ${(0.05 * (1 - d / 92)).toFixed(3)})`;
            ctx.lineWidth = 0.6;
            ctx.moveTo(a.x, a.y);
            ctx.lineTo(b.x, b.y);
            ctx.stroke();
          }
        }
      }
      ctx.globalCompositeOperation = "source-over";
    };

    const loop = (now: number) => {
      if (!running) return;
      const delta = Math.min(48, now - last);
      last = now;
      draw(delta);
      frame = requestAnimationFrame(loop);
    };

    const onPointer = (event: PointerEvent) => {
      pointer.current.tx = event.clientX / Math.max(1, width);
      pointer.current.ty = event.clientY / Math.max(1, height);
    };

    const onVisibility = () => {
      running = !document.hidden;
      if (running && !reduced) {
        last = performance.now();
        frame = requestAnimationFrame(loop);
      } else if (!running) {
        cancelAnimationFrame(frame);
      }
    };

    resize();
    if (!reduced) frame = requestAnimationFrame(loop);
    else draw(0);

    window.addEventListener("resize", resize);
    window.addEventListener("pointermove", onPointer, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      running = false;
      cancelAnimationFrame(frame);
      window.removeEventListener("resize", resize);
      window.removeEventListener("pointermove", onPointer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [reduced]);

  return (
    <>
      <div className="aurora-field" aria-hidden="true">
        <div className="aurora-blob one" />
        <div className="aurora-blob two" />
      </div>
      <canvas
        ref={canvasRef}
        className="pointer-events-none fixed inset-0 -z-10 opacity-80"
        aria-hidden="true"
      />
    </>
  );
}
