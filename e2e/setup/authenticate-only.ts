import type { FullConfig } from '@playwright/test';
import authenticate from './authenticate';
import { getE2EUser } from './user';

export default async function authenticateOnly(config: FullConfig) {
  await authenticate(config, getE2EUser());
}
