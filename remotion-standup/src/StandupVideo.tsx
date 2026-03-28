import React from "react";
import {
  AbsoluteFill,
  Audio,
  Sequence,
  useVideoConfig,
  staticFile,
} from "remotion";
import {
  TransitionSeries,
  linearTiming,
  springTiming,
} from "@remotion/transitions";
import { fade } from "@remotion/transitions/fade";
import { slide } from "@remotion/transitions/slide";
import { wipe } from "@remotion/transitions/wipe";
import type { StandupVideoProps, TransitionType } from "./types";
import { SceneRenderer } from "./components/SceneRenderer";
import { ProgressBar } from "./components/ProgressBar";
import { Waveform } from "./components/Waveform";

/**
 * Main composition: assembles all scenes with transitions, audio, and overlays.
 */
export const StandupVideo: React.FC<StandupVideoProps> = ({ timeline }) => {
  const { fps } = useVideoConfig();
  const audioSrc = staticFile(timeline.audioSrc);

  const scenes = timeline.scenes;

  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      {/* Scenes with transitions */}
      <TransitionSeries>
        {scenes.map((scene, index) => {
          const durationMs = scene.endMs - scene.startMs;
          const durationFrames = Math.round((durationMs / 1000) * fps);
          const transitionFrames = Math.round(
            (scene.transition.durationMs / 1000) * fps
          );

          return (
            <React.Fragment key={scene.id}>
              <TransitionSeries.Sequence durationInFrames={durationFrames}>
                <SceneRenderer
                  scene={scene}
                  fontFamily={timeline.fontFamily}
                  subFontFamily={timeline.subFontFamily}
                />
              </TransitionSeries.Sequence>

              {/* Add transition to next scene (except after last) */}
              {index < scenes.length - 1 && transitionFrames > 0 && (
                <TransitionSeries.Transition
                  presentation={getPresentation(scene.transition.type)}
                  timing={getTransitionTiming(
                    scene.transition.type,
                    transitionFrames
                  )}
                />
              )}
            </React.Fragment>
          );
        })}
      </TransitionSeries>

      {/* Audio layer */}
      <Audio src={audioSrc} />

      {/* Overlay: waveform */}
      {timeline.showWaveform && (
        <Waveform audioSrc={audioSrc} color={timeline.waveformColor} />
      )}

      {/* Overlay: progress bar */}
      {timeline.showProgressBar && (
        <ProgressBar color={timeline.progressBarColor} />
      )}
    </AbsoluteFill>
  );
};

function getPresentation(type: TransitionType) {
  switch (type) {
    case "slide":
      return slide({ direction: "from-left" });
    case "wipe":
      return wipe({ direction: "from-left" });
    case "fade":
    default:
      return fade();
  }
}

function getTransitionTiming(type: TransitionType, durationFrames: number) {
  if (type === "slide") {
    return springTiming({
      config: { damping: 20, stiffness: 200 },
      durationInFrames: durationFrames,
    });
  }
  return linearTiming({ durationInFrames: durationFrames });
}
