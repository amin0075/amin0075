// Refreshes the "Latest videos" block in README.md from the channel's public
// YouTube feeds: the uploads feed plus the Shorts playlist feed (UUSH…), no
// API key. Run by .github/workflows/latest-videos.yml, or locally with
// `node scripts/update-videos.mjs`.
//
// A failed fetch throws, so a YouTube hiccup never blanks the list.

import { readFile, writeFile } from "node:fs/promises";

const CHANNEL_ID = process.env.YOUTUBE_CHANNEL_ID ?? "UChxpGJZb5VkK_VsRwtP4o8A";
const SUBSCRIBE = "https://www.youtube.com/@amin_decodes?sub_confirmation=1";
const COUNT = 3;
const START = "<!-- videos:start -->";
const END = "<!-- videos:end -->";
const README = new URL("../README.md", import.meta.url);

const ENTITIES = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" };

function decode(text) {
  return text.replace(/&(#x[\da-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (match, code) => {
    if (code.startsWith("#")) {
      const hex = code[1].toLowerCase() === "x";
      return String.fromCodePoint(Number.parseInt(code.slice(hex ? 2 : 1), hex ? 16 : 10));
    }
    return ENTITIES[code.toLowerCase()] ?? match;
  });
}

async function readFeed(query, { missingIsEmpty }) {
  const url = `https://www.youtube.com/feeds/videos.xml?${query}`;
  const response = await fetch(url);
  // YouTube 404s a playlist feed until the playlist has a video in it.
  if (response.status === 404 && missingIsEmpty) return [];
  if (!response.ok) throw new Error(`${url} answered ${response.status}`);

  return (await response.text())
    .split("<entry>")
    .slice(1)
    .map((chunk) => {
      const id = /<yt:videoId>([\w-]{11})<\/yt:videoId>/.exec(chunk)?.[1];
      const title = decode(/<title>([\s\S]*?)<\/title>/.exec(chunk)?.[1] ?? "").trim();
      const published = /<published>([^<]+)<\/published>/.exec(chunk)?.[1];
      const link = /<link rel="alternate" href="([^"]+)"/.exec(chunk)?.[1] ?? "";
      if (!id || !title || !published) return null;
      return { id, title, published, short: link.includes("/shorts/") };
    })
    .filter(Boolean);
}

const attr = (text) =>
  text.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const markdown = (text) => text.replace(/([\\`*_[\]<>])/g, "\\$1");

function block(videos) {
  if (videos.length === 0) {
    return `The first videos are on the way. [Subscribe on YouTube](${SUBSCRIBE}) and they'll land in your feed.`;
  }

  const thumbnails = videos
    .map(
      (video) =>
        `<a href="${video.url}"><img src="https://i.ytimg.com/vi/${video.id}/mqdefault.jpg" width="32%" alt="${attr(video.title)}"></a>`,
    )
    .join("\n");
  const titles = videos.map((video) => `[${markdown(video.title)}](${video.url})`).join(" · ");

  return `<p>\n${thumbnails}\n</p>\n\n${titles} · [All videos](https://amin-keshavarzi.com/videos)`;
}

const [uploads, shorts] = await Promise.all([
  readFeed(`channel_id=${CHANNEL_ID}`, { missingIsEmpty: false }),
  readFeed(`playlist_id=UUSH${CHANNEL_ID.slice(2)}`, { missingIsEmpty: true }),
]);

const byId = new Map(uploads.map((video) => [video.id, video]));
for (const video of shorts) byId.set(video.id, { ...(byId.get(video.id) ?? video), short: true });

const latest = [...byId.values()]
  .sort((a, b) => Date.parse(b.published) - Date.parse(a.published))
  .slice(0, COUNT)
  .map((video) => ({
    ...video,
    url: video.short
      ? `https://www.youtube.com/shorts/${video.id}`
      : `https://www.youtube.com/watch?v=${video.id}`,
  }));

const readme = await readFile(README, "utf8");
const start = readme.indexOf(START);
const end = readme.indexOf(END);
if (start === -1 || end < start) throw new Error("README.md is missing the videos markers");

const next = `${readme.slice(0, start + START.length)}\n${block(latest)}\n${readme.slice(end)}`;
if (next === readme) {
  console.log("Latest videos already up to date.");
} else {
  await writeFile(README, next);
  console.log(`Latest videos updated: ${latest.map((video) => video.title).join(" | ") || "none yet"}`);
}
