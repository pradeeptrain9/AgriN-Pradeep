import React from 'react';
import { SafeAreaProvider } from 'react-native-safe-area-context';
import { StatusBar } from 'react-native';

import { AppNavigator } from './navigation/AppNavigator';
import { colors } from './constants/theme';

const App: React.FC = () => (
  <SafeAreaProvider>
    <StatusBar barStyle="light-content" backgroundColor={colors.primaryDark} />
    <AppNavigator />
  </SafeAreaProvider>
);

export default App;
