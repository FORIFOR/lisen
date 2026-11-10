import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "ListenFlow",
    short_name: "ListenFlow",
    description: "毎日30分から、好きなだけ学べる適応型リスニングトレーニング",
    start_url: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#4258c9",
    lang: "ja",
    icons: [
      { src: "/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icon-512.png", sizes: "512x512", type: "image/png" },
    ],
  };
}
