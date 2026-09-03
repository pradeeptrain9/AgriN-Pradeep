const { getDefaultConfig, mergeConfig } = require('@react-native/metro-config');

const defaultConfig = getDefaultConfig(__dirname);

module.exports = mergeConfig(defaultConfig, {
  resolver: {
    // Metro does not treat .tflite as an asset by default, so the model would be
    // resolved as a JS module and the bundle would fail.
    assetExts: [...defaultConfig.resolver.assetExts, 'tflite'],
  },
});
