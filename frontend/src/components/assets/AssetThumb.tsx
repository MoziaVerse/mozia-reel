import type { ReactNode } from "react";

type Variant = "display" | "picker";

interface Props {
  imageUrl: string | null | undefined;
  alt: string;
  fallback: ReactNode;
  variant: Variant;
}

const DISPLAY_BG =
  "linear-gradient(135deg, light-dark(oklch(0.97 0.004 260), oklch(0.18 0.02 260)), light-dark(oklch(0.99 0.004 260), oklch(0.14 0.02 260)))";
const PICKER_BG =
  "linear-gradient(135deg, light-dark(oklch(0.95 0.008 260), oklch(0.2 0.025 260)), light-dark(oklch(0.995 0.004 260), oklch(0.16 0.02 260)))";

export function AssetThumb({ imageUrl, alt, fallback, variant }: Props) {
  const isDisplay = variant === "display";
  const containerClass = isDisplay
    ? "aspect-video flex items-center justify-center text-text-4"
    : "aspect-video flex items-center justify-center rounded text-text-4 text-xs";
  const imgClass = isDisplay
    ? "h-full w-full object-contain"
    : "h-full w-full object-contain rounded";
  return (
    <div className={containerClass} style={{ background: isDisplay ? DISPLAY_BG : PICKER_BG }}>
      {imageUrl ? (
        <img
          src={imageUrl}
          alt={alt}
          loading="lazy"
          decoding="async"
          className={imgClass}
        />
      ) : (
        fallback
      )}
    </div>
  );
}
