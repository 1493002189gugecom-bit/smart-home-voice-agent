import type { CapacitorConfig } from "@capacitor/cli";

const config: CapacitorConfig = {
  appId: "com.smarthome.tablet",
  appName: "小屋·我的家",
  webDir: "dist",
  android: { allowMixedContent: false },
};

export default config;
