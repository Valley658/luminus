import unittest
import mysql_backend


class MySQLCompatibilityTests(unittest.TestCase):
    def test_bound_parameters_preserve_question_marks_in_literals(self):
        query = mysql_backend.translate("SELECT '?' AS example WHERE name=?")
        self.assertEqual(query, "SELECT '?' AS example WHERE name=%s")

    def test_ip_state_upsert_preserves_other_columns(self):
        query = mysql_backend.translate('INSERT INTO site_ip_states VALUES(?,1,?,?) ON CONFLICT(ip) DO UPDATE SET after_sites=1,last_seen=excluded.last_seen')
        self.assertIn('ON DUPLICATE KEY UPDATE after_sites=1,last_seen=VALUES(last_seen)', query)
        self.assertNotIn('REPLACE', query)

    def test_rows_support_existing_named_and_numbered_access(self):
        row = mysql_backend.Row([('id', 'member'), ('expires', 42)])
        self.assertEqual(row[0], row['id'])
        self.assertEqual(dict(row), {'id': 'member', 'expires': 42})

    def test_schema_uses_indexable_utf8mb4_keys_and_binary_avatar(self):
        sql = mysql_backend.translate('CREATE TABLE user_avatars(user_id TEXT PRIMARY KEY,image BLOB NOT NULL)')
        self.assertIn('user_id VARCHAR(191)', sql)
        self.assertIn('LONGBLOB', sql)
        self.assertIn('utf8mb4_bin', sql)
        self.assertIn('path VARCHAR(191)',mysql_backend.translate('CREATE TABLE path_blocks(path TEXT PRIMARY KEY)'))


if __name__ == '__main__':
    unittest.main()
