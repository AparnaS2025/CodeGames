param(
    [Parameter(Mandatory = $true)]
    [string]$ResourceGroup,

    [Parameter(Mandatory = $true)]
    [string]$FunctionAppName,

    [Parameter(Mandatory = $true)]
    [string]$StorageAccountName,

    [Parameter(Mandatory = $true)]
    [string]$CowApiBaseUrl,

    [Parameter(Mandatory = $true)]
    [string]$McpBearerToken,

    [string]$Location = "eastus",
    [string]$PythonVersion = "3.12"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    throw "Azure CLI 'az' was not found. Install Azure CLI and run 'az login' before using this script."
}

if (-not (Get-Command func -ErrorAction SilentlyContinue)) {
    throw "Azure Functions Core Tools 'func' was not found. Install Core Tools v4 before using this script."
}

az group create `
    --name $ResourceGroup `
    --location $Location

az storage account create `
    --name $StorageAccountName `
    --resource-group $ResourceGroup `
    --location $Location `
    --sku Standard_LRS `
    --allow-blob-public-access false

az functionapp create `
    --name $FunctionAppName `
    --resource-group $ResourceGroup `
    --storage-account $StorageAccountName `
    --consumption-plan-location $Location `
    --runtime python `
    --runtime-version $PythonVersion `
    --functions-version 4 `
    --os-type Linux

az functionapp config appsettings set `
    --name $FunctionAppName `
    --resource-group $ResourceGroup `
    --settings `
        "COW_API_BASE_URL=$CowApiBaseUrl" `
        "COW_MCP_BEARER_TOKEN=$McpBearerToken" `
        "PYTHON_ENABLE_INIT_INDEXING=1" `
        "SCM_DO_BUILD_DURING_DEPLOYMENT=true" `
        "ENABLE_ORYX_BUILD=true"

func azure functionapp publish $FunctionAppName --python --build remote

Write-Host ""
Write-Host "MCP endpoint:"
Write-Host "https://$FunctionAppName.azurewebsites.net/mcp"
Write-Host ""
Write-Host "FAB custom header:"
Write-Host "{ `"Authorization`": `"Bearer $McpBearerToken`" }"
