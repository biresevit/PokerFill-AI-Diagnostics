-- Grant SELECT on this view to the existing diagnostics runtime principal at deployment.
CREATE OR ALTER VIEW backoffice.DiagnosticRingScheduleHistory AS
SELECT Id AS AuditId, CustomerId, TRY_CONVERT(int, Entity) AS SiteId, CreatedUtc, DetailJson
FROM backoffice.AuditLog
WHERE Action = 'schedule.update' AND JSON_VALUE(DetailJson, '$.format') = 'RING';
