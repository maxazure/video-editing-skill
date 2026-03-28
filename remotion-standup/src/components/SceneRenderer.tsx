import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";
import type { Scene } from "../types";
import { AnimatedText } from "./AnimatedText";
import { Background } from "./Background";

interface SceneRendererProps {
  scene: Scene;
  fontFamily: string;
  subFontFamily?: string;
}

/**
 * Renders a single scene: background + animated text + optional subtext.
 * Handles scene-level fade-out before transition to next scene.
 */
export const SceneRenderer: React.FC<SceneRendererProps> = ({
  scene,
  fontFamily,
  subFontFamily,
}) => {
  const frame = useCurrentFrame();
  const { fps, durationInFrames } = useVideoConfig();

  // Fade out at the end of the scene
  const fadeOutFrames = Math.min(
    Math.round((scene.transition.durationMs / 1000) * fps),
    Math.round(durationInFrames * 0.3)
  );
  const fadeOut = interpolate(
    frame,
    [durationInFrames - fadeOutFrames, durationInFrames],
    [1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" }
  );

  return (
    <AbsoluteFill>
      <Background background={scene.background} />

      {/* Subtle vignette overlay */}
      <AbsoluteFill
        style={{
          background:
            "radial-gradient(ellipse at center, transparent 50%, rgba(0,0,0,0.4) 100%)",
        }}
      />

      {/* Main text container */}
      <AbsoluteFill
        style={{
          display: "flex",
          flexDirection: "column",
          justifyContent: "center",
          alignItems: "center",
          padding: "10% 5%",
          opacity: fadeOut,
        }}
      >
        <AnimatedText
          text={scene.text}
          animation={scene.animation}
          emphasis={scene.emphasis}
          color={scene.textColor}
          fontFamily={fontFamily}
        />

        {/* Optional secondary text */}
        {scene.subText && (
          <div
            style={{
              marginTop: 30,
              fontSize: 32,
              color: `${scene.textColor}99`,
              fontFamily: subFontFamily || fontFamily,
              textAlign: "center",
              opacity: interpolate(frame, [fps * 0.5, fps], [0, 1], {
                extrapolateLeft: "clamp",
                extrapolateRight: "clamp",
              }),
            }}
          >
            {scene.subText}
          </div>
        )}
      </AbsoluteFill>
    </AbsoluteFill>
  );
};
