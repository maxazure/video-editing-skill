import React from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig, interpolate } from "remotion";

interface ProgressBarProps {
  color: string;
}

/**
 * Bottom progress bar showing playback position.
 */
export const ProgressBar: React.FC<ProgressBarProps> = ({ color }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();

  const progress = interpolate(frame, [0, durationInFrames], [0, 100], {
    extrapolateRight: "clamp",
  });

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          bottom: 0,
          left: 0,
          right: 0,
          height: 6,
          backgroundColor: "rgba(255,255,255,0.15)",
        }}
      >
        <div
          style={{
            height: "100%",
            width: `${progress}%`,
            backgroundColor: color,
            borderRadius: "0 3px 3px 0",
            boxShadow: `0 0 12px ${color}80`,
          }}
        />
      </div>
    </AbsoluteFill>
  );
};
