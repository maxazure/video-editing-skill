import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import type { Background as BackgroundType } from "../types";

interface BackgroundProps {
  background: BackgroundType;
}

/**
 * Animated background component.
 * Supports gradient, solid, and radial backgrounds with optional animation.
 */
export const Background: React.FC<BackgroundProps> = ({ background }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const { type, colors, animateAngle } = background;

  if (type === "solid") {
    return (
      <AbsoluteFill style={{ backgroundColor: colors[0] || "#000" }} />
    );
  }

  if (type === "radial") {
    const scale = animateAngle
      ? interpolate(frame, [0, fps * 4], [1, 1.3], {
          extrapolateRight: "clamp",
        })
      : 1;
    return (
      <AbsoluteFill
        style={{
          background: `radial-gradient(circle at 50% 50%, ${colors.join(", ")})`,
          transform: `scale(${scale})`,
        }}
      />
    );
  }

  // gradient (default)
  const angle = animateAngle
    ? interpolate(frame, [0, fps * 8], [135, 225], {
        extrapolateRight: "clamp",
      })
    : 135;

  return (
    <AbsoluteFill
      style={{
        background: `linear-gradient(${angle}deg, ${colors.join(", ")})`,
      }}
    />
  );
};
