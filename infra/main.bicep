targetScope = 'resourceGroup'

@description('Short, lowercase workload prefix used in resource names.')
@minLength(3)
@maxLength(18)
param workloadName string = 'kentrick-kp'

@description('Azure region. Confirm Azure OpenAI model availability and data-residency requirements before deployment.')
param location string = resourceGroup().location

@description('Deployment environment label.')
@allowed([
  'dev'
  'test'
  'prod'
])
param environmentName string = 'dev'

@description('Immutable container reference; use a registry digest in production.')
param containerImage string

@description('Entra application/client ID accepted as the API audience.')
param entraClientId string

@description('Existing Azure OpenAI chat model deployment name. The model deployment itself is intentionally not provisioned here.')
param chatDeploymentName string

@description('Existing Azure OpenAI embedding model deployment name. The model deployment itself is intentionally not provisioned here.')
param embeddingDeploymentName string

@description('Expose the Container App directly to the public internet. Defaults to false; use APIM/App Gateway plus Entra in production.')
param allowPublicApi bool = false

@description('Minimum running API replicas. Production normally uses at least two.')
@minValue(0)
@maxValue(10)
param minReplicas int = 1

@description('Maximum running API replicas.')
@minValue(1)
@maxValue(100)
param maxReplicas int = 10

@description('Tags applied to all resources.')
param tags object = {
  workload: 'enterprise-knowledge-platform'
  environment: environmentName
  managedBy: 'bicep'
  dataClassification: 'internal'
}

var suffix = toLower(uniqueString(subscription().id, resourceGroup().id, workloadName, environmentName))
var compactName = replace(toLower(workloadName), '-', '')
var storageName = take('${compactName}${suffix}', 24)
var searchName = take('${workloadName}-${suffix}', 60)
var openAiName = take('${workloadName}-aoai-${suffix}', 64)
var keyVaultName = take('${workloadName}-kv-${suffix}', 24)
var logName = take('${workloadName}-log-${environmentName}', 63)
var appInsightsName = take('${workloadName}-appi-${environmentName}', 260)
var containerEnvironmentName = take('${workloadName}-cae-${environmentName}', 60)
var containerAppName = take('${workloadName}-api-${environmentName}', 32)

resource virtualNetwork 'Microsoft.Network/virtualNetworks@2023-11-01' = {
  name: '${workloadName}-vnet-${environmentName}'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: [
        '10.42.0.0/20'
      ]
    }
  }
}

resource containerAppsSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' = {
  parent: virtualNetwork
  name: 'container-apps'
  properties: {
    addressPrefix: '10.42.0.0/23'
    delegations: [
      {
        name: 'container-apps-delegation'
        properties: {
          serviceName: 'Microsoft.App/environments'
        }
      }
    ]
  }
}

resource privateEndpointsSubnet 'Microsoft.Network/virtualNetworks/subnets@2023-11-01' = {
  parent: virtualNetwork
  name: 'private-endpoints'
  properties: {
    addressPrefix: '10.42.2.0/24'
    privateEndpointNetworkPolicies: 'Disabled'
  }
}

resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: logName
  location: location
  tags: tags
  properties: {
    retentionInDays: 30
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource applicationInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: appInsightsName
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    DisableLocalAuth: true
    IngestionMode: 'LogAnalytics'
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  tags: tags
  sku: {
    name: 'Standard_ZRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowCrossTenantReplication: false
    allowSharedKeyAccess: false
    defaultToOAuthAuthentication: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Disabled'
    supportsHttpsTrafficOnly: true
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Deny'
      ipRules: []
      virtualNetworkRules: []
    }
    encryption: {
      keySource: 'Microsoft.Storage'
      requireInfrastructureEncryption: true
      services: {
        blob: {
          enabled: true
          keyType: 'Account'
        }
      }
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 30
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 30
    }
    isVersioningEnabled: true
  }
}

resource documentContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'documents'
  properties: {
    publicAccess: 'None'
  }
}

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'standard'
  }
  properties: {
    disableLocalAuth: true
    hostingMode: 'default'
    partitionCount: 1
    publicNetworkAccess: 'disabled'
    replicaCount: environmentName == 'prod' ? 2 : 1
    semanticSearch: environmentName == 'prod' ? 'standard' : 'free'
  }
}

resource openAi 'Microsoft.CognitiveServices/accounts@2023-05-01' = {
  name: openAiName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: openAiName
    publicNetworkAccess: 'Disabled'
    restrictOutboundNetworkAccess: true
    networkAcls: {
      defaultAction: 'Deny'
      ipRules: []
      virtualNetworkRules: []
    }
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    tenantId: tenant().tenantId
    sku: {
      family: 'A'
      name: 'standard'
    }
    enableRbacAuthorization: true
    enablePurgeProtection: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      bypass: 'None'
      defaultAction: 'Deny'
      ipRules: []
      virtualNetworkRules: []
    }
  }
}

var privateDnsZoneNames = [
  'privatelink.blob.core.windows.net'
  'privatelink.search.windows.net'
  'privatelink.openai.azure.com'
  'privatelink.vaultcore.azure.net'
]

resource privateDnsZones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for zoneName in privateDnsZoneNames: {
  name: zoneName
  location: 'global'
  tags: tags
}]

resource privateDnsLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for (zoneName, index) in privateDnsZoneNames: {
  parent: privateDnsZones[index]
  name: '${workloadName}-${environmentName}-link'
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: {
      id: virtualNetwork.id
    }
  }
}]

var privateEndpointConfigurations = [
  {
    name: 'blob'
    serviceId: storage.id
    groupId: 'blob'
    dnsZoneIndex: 0
  }
  {
    name: 'search'
    serviceId: search.id
    groupId: 'searchService'
    dnsZoneIndex: 1
  }
  {
    name: 'openai'
    serviceId: openAi.id
    groupId: 'account'
    dnsZoneIndex: 2
  }
  {
    name: 'vault'
    serviceId: keyVault.id
    groupId: 'vault'
    dnsZoneIndex: 3
  }
]

resource privateEndpoints 'Microsoft.Network/privateEndpoints@2023-11-01' = [for endpoint in privateEndpointConfigurations: {
  name: '${workloadName}-pe-${endpoint.name}-${environmentName}'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointsSubnet.id
    }
    privateLinkServiceConnections: [
      {
        name: '${endpoint.name}-connection'
        properties: {
          privateLinkServiceId: endpoint.serviceId
          groupIds: [
            endpoint.groupId
          ]
        }
      }
    ]
  }
}]

resource privateDnsZoneGroups 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = [for (endpoint, index) in privateEndpointConfigurations: {
  parent: privateEndpoints[index]
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: privateDnsZoneNames[endpoint.dnsZoneIndex]
        properties: {
          privateDnsZoneId: privateDnsZones[endpoint.dnsZoneIndex].id
        }
      }
    ]
  }
}]

resource containerEnvironment 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: containerEnvironmentName
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logAnalytics.properties.customerId
        sharedKey: logAnalytics.listKeys().primarySharedKey
      }
    }
    vnetConfiguration: {
      infrastructureSubnetId: containerAppsSubnet.id
      internal: !allowPublicApi
    }
  }
}

resource api 'Microsoft.App/containerApps@2024-03-01' = {
  name: containerAppName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    environmentId: containerEnvironment.id
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: allowPublicApi
        allowInsecure: false
        targetPort: 8000
        transport: 'http'
        traffic: [
          {
            latestRevision: true
            weight: 100
          }
        ]
      }
    }
    template: {
      containers: [
        {
          name: 'api'
          image: containerImage
          env: [
            {
              name: 'APP_ENV'
              value: environmentName
            }
            {
              name: 'AUTH_MODE'
              value: 'entra'
            }
            {
              name: 'AZURE_TENANT_ID'
              value: tenant().tenantId
            }
            {
              name: 'ENTRA_AUDIENCE'
              value: 'api://${entraClientId}'
            }
            {
              name: 'ENTRA_ISSUER'
              value: 'https://login.microsoftonline.com/${tenant().tenantId}/v2.0'
            }
            {
              name: 'BACKEND'
              value: 'azure'
            }
            {
              name: 'AZURE_SEARCH_ENDPOINT'
              value: 'https://${search.name}.search.windows.net'
            }
            {
              name: 'AZURE_SEARCH_INDEX'
              value: 'knowledge-chunks-v1'
            }
            {
              name: 'AZURE_OPENAI_ENDPOINT'
              value: 'https://${openAi.name}.openai.azure.com/'
            }
            {
              name: 'AZURE_OPENAI_CHAT_DEPLOYMENT'
              value: chatDeploymentName
            }
            {
              name: 'AZURE_OPENAI_EMBEDDING_DEPLOYMENT'
              value: embeddingDeploymentName
            }
            {
              name: 'AZURE_STORAGE_ACCOUNT_URL'
              value: 'https://${storage.name}.blob.core.windows.net'
            }
            {
              name: 'AZURE_KEY_VAULT_URI'
              value: keyVault.properties.vaultUri
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: applicationInsights.properties.ConnectionString
            }
            {
              name: 'ALLOW_FAILURE_INJECTION'
              value: 'false'
            }
          ]
          probes: [
            {
              type: 'Liveness'
              httpGet: {
                path: '/health/live'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 10
              periodSeconds: 30
              timeoutSeconds: 3
              failureThreshold: 3
            }
            {
              type: 'Readiness'
              httpGet: {
                path: '/health/ready'
                port: 8000
                scheme: 'HTTP'
              }
              initialDelaySeconds: 5
              periodSeconds: 10
              timeoutSeconds: 3
              failureThreshold: 3
            }
          ]
          resources: {
            cpu: json('0.5')
            memory: '1Gi'
          }
        }
      ]
      scale: {
        minReplicas: minReplicas
        maxReplicas: maxReplicas
        rules: [
          {
            name: 'http-concurrency'
            http: {
              metadata: {
                concurrentRequests: '20'
              }
            }
          }
        ]
      }
    }
  }
}

var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
var cognitiveServicesOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'

resource apiSearchRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, api.id, searchIndexDataContributorRoleId)
  scope: search
  properties: {
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
  }
}

resource apiOpenAiRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAi.id, api.id, cognitiveServicesOpenAIUserRoleId)
  scope: openAi
  properties: {
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
  }
}

resource apiStorageRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, api.id, storageBlobDataContributorRoleId)
  scope: storage
  properties: {
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
  }
}

resource apiKeyVaultRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, api.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    principalId: api.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
  }
}

output containerAppName string = api.name
output containerAppFqdn string = allowPublicApi ? api.properties.configuration.ingress.fqdn : 'internal-only'
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output openAiEndpoint string = 'https://${openAi.name}.openai.azure.com/'
output storageAccountName string = storage.name
output keyVaultUri string = keyVault.properties.vaultUri
output managedIdentityPrincipalId string = api.identity.principalId
