import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import React, { useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';

import { colors } from '../constants/theme';
import { initDb } from '../db';
import { ConsentScreen } from '../screens/auth/ConsentScreen';
import { OtpScreen } from '../screens/auth/OtpScreen';
import { PhoneScreen } from '../screens/auth/PhoneScreen';
import { ScanScreen } from '../screens/disease/ScanScreen';
import { CropScreen } from '../screens/fields/CropScreen';
import { DrawFieldScreen } from '../screens/fields/DrawFieldScreen';
import { FieldDetailScreen } from '../screens/fields/FieldDetailScreen';
import { FieldListScreen } from '../screens/fields/FieldListScreen';
import { CropSuggestionScreen } from '../screens/fields/CropSuggestionScreen';
import { MapFieldScreen } from '../screens/fields/MapFieldScreen';
import { SoilCardScreen } from '../screens/fields/SoilCardScreen';
import { SettingsScreen } from '../screens/settings/SettingsScreen';
import { loadNodeUrl } from '../services/node';
import { loadModel } from '../services/tflite';
import { useAuthStore } from '../store/authSlice';

const Stack = createNativeStackNavigator();

/** Header action with a target big enough to hit outdoors. */
const HeaderButton: React.FC<{ label: string; onPress: () => void }> = ({
  label, onPress,
}) => (
  <Pressable
    onPress={onPress}
    accessibilityRole="button"
    accessibilityLabel={label}
    hitSlop={12}
    style={{ paddingHorizontal: 8, paddingVertical: 8 }}
  >
    <Text style={{ color: colors.onPrimary, fontSize: 16, fontWeight: '600' }}>
      {label}
    </Text>
  </Pressable>
);

const screenOptions = {
  headerStyle: { backgroundColor: colors.primary },
  headerTintColor: colors.onPrimary,
  headerTitleStyle: { fontSize: 20, fontWeight: '700' as const },
};

export const AppNavigator: React.FC = () => {
  const { isAuthenticated, isLoading, consentAcceptedAt, loadFromStorage } =
    useAuthStore();

  const [nodeReady, setNodeReady] = useState(false);

  useEffect(() => {
    initDb();            // mirror must exist before any screen reads it
    // The node URL must be resolved before ANY request is made. Loading it in
    // the sign-in screen was wrong: an already-signed-in user never mounts that
    // screen, so every request fell back to the default host and the app looked
    // permanently offline from the second launch onward.
    loadNodeUrl().finally(() => setNodeReady(true));
    // Warm the classifier in the background so the first scan is not also the
    // first load. Failure is silent by design: every photo then goes to the
    // server, which is the same path an unsupported crop takes.
    loadModel().catch(() => {});
    loadFromStorage();
  }, [loadFromStorage]);

  if (isLoading || !nodeReady) {
    return (
      <View style={{ flex: 1, justifyContent: 'center', backgroundColor: colors.bg }}>
        <ActivityIndicator size="large" color={colors.primary} />
      </View>
    );
  }

  return (
    <NavigationContainer>
      <Stack.Navigator screenOptions={screenOptions}>
        {!isAuthenticated ? (
          <>
            <Stack.Screen name="Phone" component={PhoneScreen} options={{ headerShown: false }} />
            <Stack.Screen name="Otp" component={OtpScreen} options={{ title: 'Verify' }} />
          </>
        ) : !consentAcceptedAt ? (
          // A gate, not a banner. There is no route into the app until the
          // farmer has read what happens to their data and tapped to say so.
          <Stack.Screen
            name="Consent" component={ConsentScreen} options={{ headerShown: false }}
          />
        ) : (
          <>
            <Stack.Screen
              name="Fields"
              component={FieldListScreen}
              options={({ navigation }) => ({
                title: 'My fields',
                headerRight: () => (
                  <HeaderButton
                    label="Settings"
                    onPress={() => navigation.navigate('Settings')}
                  />
                ),
              })}
            />
            <Stack.Screen
              name="Settings" component={SettingsScreen} options={{ title: 'Settings' }}
            />
            <Stack.Screen
              name="CropSuggestion"
              component={CropSuggestionScreen}
              options={{ title: 'What to grow' }}
            />
            <Stack.Screen
              name="MapField" component={MapFieldScreen} options={{ title: 'Walk a field' }}
            />
            <Stack.Screen
              name="DrawField" component={DrawFieldScreen} options={{ title: 'Draw a field' }}
            />
            <Stack.Screen
              name="FieldDetail" component={FieldDetailScreen} options={{ title: 'Field' }}
            />
            <Stack.Screen
              name="Crop" component={CropScreen} options={{ title: 'Your crop' }}
            />
            <Stack.Screen
              name="SoilCard" component={SoilCardScreen} options={{ title: 'Your soil' }}
            />
            <Stack.Screen
              name="Scan" component={ScanScreen} options={{ title: 'Check a leaf' }}
            />
          </>
        )}
      </Stack.Navigator>
    </NavigationContainer>
  );
};
