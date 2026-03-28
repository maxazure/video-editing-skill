import React from "react";
import {
  useCurrentFrame,
  useVideoConfig,
  interpolate,
  spring,
  Easing,
} from "remotion";
import type { TextAnimation } from "../types";

interface AnimatedTextProps {
  text: string;
  animation: TextAnimation;
  emphasis: number;
  color: string;
  fontFamily: string;
}

/**
 * Core animated text component.
 * Renders a single text block with the specified animation effect.
 */
export const AnimatedText: React.FC<AnimatedTextProps> = ({
  text,
  animation,
  emphasis,
  color,
  fontFamily,
}) => {
  const frame = useCurrentFrame();
  const { fps, width } = useVideoConfig();

  const baseFontSize = 72 * emphasis;
  const style = getAnimationStyle(animation, frame, fps, baseFontSize);

  // For typewriter effect, we truncate the text
  const displayText =
    animation === "typewriter" ? getTypewriterText(text, frame, fps) : text;

  // For wave effect, render per-character
  if (animation === "wave") {
    return (
      <div
        style={{
          display: "flex",
          flexWrap: "wrap",
          justifyContent: "center",
          alignItems: "center",
          maxWidth: width * 0.85,
          fontFamily,
        }}
      >
        {text.split("").map((char, i) => {
          const delay = i * 2;
          const charSpring = spring({
            frame: Math.max(0, frame - delay),
            fps,
            config: { damping: 12, stiffness: 200 },
          });
          return (
            <span
              key={i}
              style={{
                display: "inline-block",
                fontSize: baseFontSize,
                color,
                opacity: charSpring,
                transform: `translateY(${interpolate(charSpring, [0, 1], [30, 0])}px)`,
                whiteSpace: "pre",
              }}
            >
              {char}
            </span>
          );
        })}
      </div>
    );
  }

  return (
    <div
      style={{
        fontSize: baseFontSize,
        color,
        fontFamily,
        textAlign: "center",
        maxWidth: width * 0.85,
        lineHeight: 1.3,
        fontWeight: emphasis >= 1.5 ? 900 : 700,
        textShadow:
          emphasis >= 1.5
            ? `0 0 40px ${color}40, 0 4px 8px rgba(0,0,0,0.5)`
            : "0 2px 4px rgba(0,0,0,0.3)",
        wordBreak: "break-word",
        ...style,
      }}
    >
      {displayText}
      {animation === "typewriter" && frame % (fps / 2) < fps / 4 && (
        <span style={{ opacity: 0.8 }}>|</span>
      )}
    </div>
  );
};

function getTypewriterText(text: string, frame: number, fps: number): string {
  const charsPerSecond = 15;
  const charCount = Math.floor((frame / fps) * charsPerSecond);
  return text.slice(0, charCount);
}

function getAnimationStyle(
  animation: TextAnimation,
  frame: number,
  fps: number,
  _fontSize: number
): React.CSSProperties {
  switch (animation) {
    case "fadeIn": {
      const opacity = interpolate(frame, [0, fps * 0.5], [0, 1], {
        extrapolateRight: "clamp",
      });
      return { opacity };
    }

    case "springIn": {
      const progress = spring({
        frame,
        fps,
        config: { damping: 15, stiffness: 150 },
      });
      return {
        opacity: progress,
        transform: `translateY(${interpolate(progress, [0, 1], [60, 0])}px)`,
      };
    }

    case "scaleUp": {
      const progress = spring({
        frame,
        fps,
        config: { damping: 12, stiffness: 100 },
      });
      return {
        opacity: Math.min(progress * 2, 1),
        transform: `scale(${interpolate(progress, [0, 1], [0.3, 1])})`,
      };
    }

    case "scaleDown": {
      const progress = spring({
        frame,
        fps,
        config: { damping: 20, stiffness: 200 },
      });
      return {
        opacity: Math.min(progress * 2, 1),
        transform: `scale(${interpolate(progress, [0, 1], [2.5, 1])})`,
      };
    }

    case "bounce": {
      const progress = spring({
        frame,
        fps,
        config: { damping: 6, stiffness: 150, mass: 1 },
      });
      return {
        opacity: Math.min(frame / (fps * 0.1), 1),
        transform: `scale(${interpolate(progress, [0, 1], [0, 1])})`,
      };
    }

    case "shake": {
      const enterOpacity = interpolate(frame, [0, fps * 0.15], [0, 1], {
        extrapolateRight: "clamp",
      });
      // Shake intensely for the first 0.5s, then settle
      const shakeIntensity = interpolate(
        frame,
        [0, fps * 0.5],
        [12, 0],
        { extrapolateRight: "clamp" }
      );
      const shakeX = Math.sin(frame * 1.5) * shakeIntensity;
      const shakeY = Math.cos(frame * 2.1) * shakeIntensity * 0.5;
      const shakeRotate = Math.sin(frame * 1.8) * shakeIntensity * 0.3;
      return {
        opacity: enterOpacity,
        transform: `translate(${shakeX}px, ${shakeY}px) rotate(${shakeRotate}deg)`,
      };
    }

    case "slam": {
      const progress = spring({
        frame,
        fps,
        config: { damping: 30, stiffness: 400, mass: 0.8 },
      });
      return {
        opacity: Math.min(frame / (fps * 0.05), 1),
        transform: `scale(${interpolate(progress, [0, 1], [4, 1])})`,
      };
    }

    case "glitch": {
      const enterOpacity = interpolate(frame, [0, fps * 0.3], [0, 1], {
        extrapolateRight: "clamp",
      });
      // Glitch flicker for first 0.5s
      const isGlitching = frame < fps * 0.5;
      const glitchOffset = isGlitching
        ? Math.sin(frame * 7) * 5 * (1 - frame / (fps * 0.5))
        : 0;
      const glitchSkew = isGlitching
        ? Math.cos(frame * 11) * 3 * (1 - frame / (fps * 0.5))
        : 0;
      return {
        opacity: isGlitching
          ? enterOpacity * (Math.random() > 0.1 ? 1 : 0.3)
          : 1,
        transform: `translate(${glitchOffset}px, 0) skewX(${glitchSkew}deg)`,
      };
    }

    case "rotateIn": {
      const progress = spring({
        frame,
        fps,
        config: { damping: 15, stiffness: 100 },
      });
      return {
        opacity: progress,
        transform: `rotate(${interpolate(progress, [0, 1], [-15, 0])}deg) scale(${interpolate(progress, [0, 1], [0.8, 1])})`,
      };
    }

    case "splitReveal": {
      const progress = spring({
        frame,
        fps,
        config: { damping: 20, stiffness: 120 },
      });
      return {
        opacity: progress,
        clipPath: `inset(0 ${interpolate(progress, [0, 1], [50, 0])}% 0 ${interpolate(progress, [0, 1], [50, 0])}%)`,
      };
    }

    case "typewriter":
      return { opacity: 1 };

    case "wave":
      // Handled separately in the component
      return {};

    default:
      return { opacity: 1 };
  }
}
