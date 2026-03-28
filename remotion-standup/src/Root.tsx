import React from "react";
import { Composition, staticFile } from "remotion";
import { StandupVideo } from "./StandupVideo";
import type { Timeline, StandupVideoProps } from "./types";

/**
 * Register all compositions for Remotion.
 *
 * The timeline is loaded from public/timeline.json by default.
 * Override with --props flag when rendering:
 *   npx remotion render StandupVideo out/video.mp4 --props=public/timeline.json
 */

// Default timeline for development preview
const defaultTimeline: Timeline = {
  fps: 30,
  width: 1080,
  height: 1920,
  audioSrc: "audio/standup.wav",
  fontFamily: "Noto Sans SC, sans-serif",
  scenes: [
    {
      id: 1,
      startMs: 0,
      endMs: 3000,
      text: "大家好",
      animation: "springIn",
      emphasis: 1.2,
      background: { type: "gradient", colors: ["#0f0c29", "#302b63", "#24243e"] },
      textColor: "#ffffff",
      transition: { type: "fade", durationMs: 300 },
    },
    {
      id: 2,
      startMs: 3000,
      endMs: 7000,
      text: "今天我要给大家讲一个段子",
      animation: "scaleUp",
      emphasis: 1.0,
      background: { type: "gradient", colors: ["#1a1a2e", "#16213e", "#0f3460"] },
      textColor: "#ffffff",
      transition: { type: "slide", durationMs: 400 },
    },
    {
      id: 3,
      startMs: 7000,
      endMs: 11000,
      text: "有人问我\n你为什么这么搞笑",
      animation: "typewriter",
      emphasis: 1.0,
      background: { type: "gradient", colors: ["#2d1b69", "#11998e"] },
      textColor: "#f0e68c",
      transition: { type: "fade", durationMs: 300 },
    },
    {
      id: 4,
      startMs: 11000,
      endMs: 14000,
      text: "因为我的人生\n就是个笑话",
      animation: "slam",
      emphasis: 1.8,
      background: { type: "radial", colors: ["#e94560", "#1a1a2e"] },
      textColor: "#ffffff",
      transition: { type: "wipe", durationMs: 500 },
    },
    {
      id: 5,
      startMs: 14000,
      endMs: 16000,
      text: "谢谢大家",
      animation: "wave",
      emphasis: 1.3,
      background: { type: "gradient", colors: ["#0f0c29", "#302b63", "#24243e"] },
      textColor: "#FFD700",
      transition: { type: "none", durationMs: 0 },
    },
  ],
  showProgressBar: true,
  progressBarColor: "#e94560",
  showWaveform: true,
  waveformColor: "#ffffff",
};

export const RemotionRoot: React.FC = () => {
  const totalDurationMs =
    defaultTimeline.scenes[defaultTimeline.scenes.length - 1].endMs;
  const durationInFrames = Math.ceil(
    (totalDurationMs / 1000) * defaultTimeline.fps
  );

  return (
    <>
      <Composition
        id="StandupVideo"
        component={StandupVideo}
        durationInFrames={durationInFrames}
        fps={defaultTimeline.fps}
        width={defaultTimeline.width}
        height={defaultTimeline.height}
        defaultProps={{
          timeline: defaultTimeline,
        }}
        calculateMetadata={async ({ props }) => {
          const tl = (props as StandupVideoProps).timeline;
          const lastScene = tl.scenes[tl.scenes.length - 1];
          const totalMs = lastScene.endMs;
          // Subtract transition overlaps
          let transitionMs = 0;
          for (let i = 0; i < tl.scenes.length - 1; i++) {
            transitionMs += tl.scenes[i].transition.durationMs;
          }
          const effectiveMs = totalMs - transitionMs;
          return {
            durationInFrames: Math.ceil((effectiveMs / 1000) * tl.fps),
            fps: tl.fps,
            width: tl.width,
            height: tl.height,
          };
        }}
      />
    </>
  );
};
