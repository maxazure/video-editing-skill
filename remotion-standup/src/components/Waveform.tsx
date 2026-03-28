import React from "react";
import {
  AbsoluteFill,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { useAudioData, visualizeAudio } from "@remotion/media-utils";

interface WaveformProps {
  audioSrc: string;
  color: string;
}

/**
 * Audio-reactive waveform bars at the bottom of the screen.
 * Shows a subtle visualization synced to the audio.
 */
export const Waveform: React.FC<WaveformProps> = ({ audioSrc, color }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const audioData = useAudioData(audioSrc);

  if (!audioData) return null;

  const numberOfBars = 32;
  const visualization = visualizeAudio({
    fps,
    frame,
    audioData,
    numberOfSamples: numberOfBars,
    smoothing: true,
  });

  return (
    <AbsoluteFill>
      <div
        style={{
          position: "absolute",
          bottom: 12,
          left: "5%",
          right: "5%",
          height: 60,
          display: "flex",
          alignItems: "flex-end",
          justifyContent: "center",
          gap: 3,
        }}
      >
        {visualization.map((amplitude, i) => {
          const height = Math.max(3, amplitude * 55);
          // Fade edges for aesthetic look
          const edgeFade =
            1 -
            Math.abs(i - numberOfBars / 2) / (numberOfBars / 2) * 0.5;
          return (
            <div
              key={i}
              style={{
                width: `${90 / numberOfBars}%`,
                height: `${height}px`,
                backgroundColor: color,
                opacity: 0.5 * edgeFade,
                borderRadius: 2,
                transition: "height 0.05s",
              }}
            />
          );
        })}
      </div>
    </AbsoluteFill>
  );
};
