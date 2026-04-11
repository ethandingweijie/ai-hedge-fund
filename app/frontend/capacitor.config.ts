import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: 'com.aihedgefund.app',
  appName: 'AI Hedge Fund',
  webDir: 'dist',
  ios: {
    contentInset: 'always',
    scrollEnabled: false,
  },
};

export default config;
