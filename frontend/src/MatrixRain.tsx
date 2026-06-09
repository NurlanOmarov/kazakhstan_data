import { useEffect, useRef } from "react";

type MatrixRainProps = {
  /** Цвет символов (по умолчанию классический зелёный) */
  color?: string;
  /** Размер шрифта символов в px (по умолчанию 16) */
  fontSize?: number;
  /** Скорость: вероятность сброса колонки наверх, 0–1 (по умолчанию 0.975) */
  speed?: number;
  className?: string;
};

// Катакана + цифры + немного латиницы — как в фильме «Матрица».
const GLYPHS =
  "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン0123456789";

/**
 * Классический «матричный дождь» зелёными символами на canvas.
 * Учитывает devicePixelRatio, ресайз окна и prefers-reduced-motion.
 */
export function MatrixRain({
  color = "#22c55e",
  fontSize = 16,
  speed = 0.975,
  className,
}: MatrixRainProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const reduced = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;

    let width = 0;
    let height = 0;
    let columns: number[] = [];
    let dpr = 1;

    const resize = () => {
      dpr = window.devicePixelRatio || 1;
      width = canvas.clientWidth;
      height = canvas.clientHeight;
      canvas.width = Math.floor(width * dpr);
      canvas.height = Math.floor(height * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const colCount = Math.ceil(width / fontSize);
      columns = Array.from({ length: colCount }, () =>
        Math.floor((Math.random() * height) / fontSize),
      );
      // Чёрный фон под символами
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, width, height);
    };

    const draw = () => {
      // Полупрозрачная заливка — оставляет «хвосты» затухания
      ctx.fillStyle = "rgba(0, 0, 0, 0.07)";
      ctx.fillRect(0, 0, width, height);
      ctx.font = `${fontSize}px monospace`;

      for (let i = 0; i < columns.length; i++) {
        const char = GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
        const x = i * fontSize;
        const y = columns[i] * fontSize;

        // Ведущий символ ярче остального следа
        ctx.fillStyle = "#d7ffe4";
        ctx.fillText(char, x, y);
        ctx.fillStyle = color;
        ctx.fillText(char, x, y);

        if (y > height && Math.random() > speed) {
          columns[i] = 0;
        } else {
          columns[i]++;
        }
      }
    };

    resize();

    if (reduced) {
      // Без анимации — один статичный кадр
      ctx.font = `${fontSize}px monospace`;
      ctx.fillStyle = color;
      for (let i = 0; i < columns.length; i++) {
        const char = GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
        ctx.fillText(char, i * fontSize, columns[i] * fontSize);
      }
      return;
    }

    let frame = 0;
    let raf = 0;
    const tick = () => {
      // ~30 fps — достаточно для эффекта и щадит CPU
      if (frame++ % 2 === 0) draw();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);

    window.addEventListener("resize", resize);
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, [color, fontSize, speed]);

  return (
    <canvas
      ref={canvasRef}
      className={`matrix-rain${className ? ` ${className}` : ""}`}
      aria-hidden="true"
    />
  );
}
