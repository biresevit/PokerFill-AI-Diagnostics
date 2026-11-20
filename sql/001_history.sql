-- Apply once with an administrator. The runtime identity receives no DDL rights.
IF SCHEMA_ID(N'diagnostics') IS NULL EXEC(N'CREATE SCHEMA diagnostics');
IF OBJECT_ID(N'diagnostics.Investigation',N'U') IS NULL
BEGIN
 CREATE TABLE diagnostics.Investigation(
  id varchar(36) NOT NULL PRIMARY KEY,
  [owner] char(64) NOT NULL,
  scope nvarchar(2000) NOT NULL,
  prompt nvarchar(max) NOT NULL,
  status varchar(20) NOT NULL,
  created_utc varchar(40) NOT NULL,
  result nvarchar(max) NULL,
  error nvarchar(4000) NULL,
  parent_id varchar(36) NULL,
  root_id varchar(36) NOT NULL,
  turn_index int NOT NULL DEFAULT 0
 );
 CREATE INDEX ix_diagnostics_owner ON diagnostics.Investigation([owner],created_utc);
 CREATE INDEX ix_diagnostics_thread ON diagnostics.Investigation([owner],root_id,turn_index);
 CREATE UNIQUE INDEX ix_diagnostics_parent ON diagnostics.Investigation(parent_id) WHERE parent_id IS NOT NULL;
END;
