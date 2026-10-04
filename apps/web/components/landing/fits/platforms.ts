/** The five illustrated Customer Surfaces and their release-specific roles. */
export type Platform = Readonly<{
  id: string;
  name: string;
  logo: string;
  caption: string;
}>;

export const PLATFORMS: readonly Platform[] = [
  {
    id: "discord",
    name: "Discord",
    logo: "/source-logos/discord.svg",
    caption: "Discord · community server: community feedback and requests can become Product Signals.",
  },
  {
    id: "reddit",
    name: "Reddit",
    logo: "/source-logos/reddit.svg",
    caption: "Reddit · public forum: customer discussions can become Product Signals.",
  },
  {
    id: "app-store",
    name: "App Store",
    logo: "/source-logos/app-store.svg",
    caption: "App Store · app reviews: reported failures and missing capabilities inform Product Signals.",
  },
  {
    id: "x",
    name: "X",
    logo: "/source-logos/x.svg",
    caption: "X · social platform: public customer feedback can become Product Signals.",
  },
  {
    id: "zendesk",
    name: "Zendesk",
    logo: "/source-logos/zendesk.svg",
    caption: "Zendesk · support system: customer support conversations can become Product Signals.",
  },
];
