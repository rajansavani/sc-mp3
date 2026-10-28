// The audio line under the review, such as "AAC 160 kbps stream → MP3 320 kbps".
import { strings } from "../strings/index.js";

const text = strings.main;

// The audio line for a job, such as "AAC 160 kbps stream → MP3 320 kbps", or null.
export function audioLine(job, settings) {
  if (job.kind === "retag") return text.retagAudio;
  const format = job.format?.format;
  if (!format) return null;
  const target = settings?.audio_format ?? "mp3_320";
  const from = audioSource(format);
  if (isCopied(format, target)) return text.audioCopied(from);
  return text.audioConverted(from, text.audioTargets[target]);
}

function codecName(format) {
  const codec = format.codec ?? format.ext;
  return text.codecNames[codec] ?? codec.toUpperCase();
}

function audioSource(format) {
  if (format.is_original) return text.audioOriginal(codecName(format));
  return text.audioStream(codecName(format), format.bitrate ? Math.round(format.bitrate) : null);
}

// MP3 sources stay as they are, and so does AAC when it's kept as .m4a.
function isCopied(format, target) {
  const codec = format.codec ?? format.ext;
  if (codec === "mp3") return target.startsWith("mp3");
  return codec === "aac" && target === "m4a";
}
