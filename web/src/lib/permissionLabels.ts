/**
 * Each permission's label (task `B-257`), for saying what a refusal is about without loading
 * the people list (which needs `access.manage`). A copy of `meridian_core.permissions`;
 * `tests/unit/test_route_permissions.py` holds the two to each other.
 */
export const BY_NAME_LABEL: Readonly<Record<string, string>> = {
  'explore.read': 'Read the corpus',
  'admin.view': 'Open Admin',
  'work.write': 'Notes and saved views',
  'chat.ask': 'Ask',
  'topics.steer': 'Topics and steering',
  'seeds.add': 'Seeds',
  'gazetteer.curate': 'Gazetteer',
  'duplicates.decide': 'Duplicates',
  'fetch_policy.edit': 'Fetch policy',
  'budget.edit': 'Budget',
  'models.edit': 'Models',
  'settings.edit': 'Display settings',
  'access.manage': 'People & permissions',
}
