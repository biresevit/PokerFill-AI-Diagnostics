from pokerfill_ai.history_sql import translate,Record


def test_sql_history_keeps_values_bound_and_uses_fixed_table():
    assert translate('SELECT * FROM investigations WHERE owner=? ORDER BY turn_index LIMIT ?')==(
        'SELECT * FROM [diagnostics].[Investigation] WHERE [owner]=%s ORDER BY turn_index OFFSET 0 ROWS FETCH NEXT %s ROWS ONLY')
    assert translate('UPDATE investigations SET result=? WHERE id=?')=='UPDATE [diagnostics].[Investigation] SET result=%s WHERE id=%s'


def test_history_rows_support_named_and_count_access():
    row=Record([('count',2)])
    assert row[0]==row['count']==2 and dict(row)=={'count':2}
