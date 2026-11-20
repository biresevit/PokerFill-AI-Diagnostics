param location string = resourceGroup().location
param appName string
param planName string = '${appName}-plan'
param sqlServer string
param sqlDatabase string
param sqlUser string = 'pokerfill_ai_app'
@secure()
param sqlPassword string
@secure()
param openAIKey string
@secure()
@minLength(32)
param brokerToken string
param model string = 'gpt-6.2-sol'
param storageAccountName string
param storageResourceGroup string

resource plan 'Microsoft.Web/serverfarms@2024-11-01' = {
  name: planName
  location: location
  kind: 'linux'
  sku: { name: 'B1', tier: 'Basic', capacity: 1 }
  properties: { reserved: true }
}

resource app 'Microsoft.Web/sites@2024-11-01' = {
  name: appName
  location: location
  kind: 'app,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    clientAffinityEnabled: false
    siteConfig: {
      linuxFxVersion: 'PYTHON|3.11'
      appCommandLine: 'bash startup.sh'
      alwaysOn: true
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      healthCheckPath: '/health'
      appSettings: [
        { name: 'SCM_DO_BUILD_DURING_DEPLOYMENT', value: 'true' }
        { name: 'ENABLE_ORYX_BUILD', value: 'true' }
        { name: 'PYTHONPATH', value: 'src:vendor/api' }
        { name: 'POKERFILL_BACKOFFICE_API', value: 'vendor/api' }
        { name: 'POKERFILL_HISTORY_BACKEND', value: 'azure-sql' }
        { name: 'POKERFILL_DIAGNOSTICS_MODE', value: 'live' }
        { name: 'POKERFILL_DIAGNOSTICS_MODEL', value: model }
        { name: 'POKERFILL_DIAGNOSTICS_COLLECT_LOGS', value: '1' }
        { name: 'POKERFILL_DIAGNOSTICS_TOKEN', value: brokerToken }
        { name: 'OPENAI_API_KEY', value: openAIKey }
        { name: 'BACKOFFICE_SQL_SERVER', value: sqlServer }
        { name: 'BACKOFFICE_SQL_DATABASE', value: sqlDatabase }
        { name: 'BACKOFFICE_SQL_USER', value: sqlUser }
        { name: 'BACKOFFICE_SQL_PASSWORD', value: sqlPassword }
        { name: 'POKERFILL_LOG_STORAGE_URL', value: 'https://${storageAccountName}.blob.${environment().suffixes.storage}' }
      ]
    }
  }
}

module logs 'blob-reader.bicep' = {
  name: '${appName}-log-access'
  scope: resourceGroup(storageResourceGroup)
  params: {
    accountName: storageAccountName
    principalId: app.identity.principalId
  }
}

output serviceUrl string = 'https://${app.properties.defaultHostName}'
output managedIdentity string = app.identity.principalId
