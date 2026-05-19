{{/*
Expand the name of the chart.
*/}}
{{- define "nebari-data-science-pack.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "nebari-data-science-pack.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "nebari-data-science-pack.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "nebari-data-science-pack.labels" -}}
helm.sh/chart: {{ include "nebari-data-science-pack.chart" . }}
{{ include "nebari-data-science-pack.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Singleuser config ConfigMap name
*/}}
{{- define "nebari-data-science-pack.singleuser-config" -}}
{{- printf "%s-singleuser-config" (include "nebari-data-science-pack.name" .) -}}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "nebari-data-science-pack.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nebari-data-science-pack.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Effective NFS-server enablement.
The chart's in-cluster NFS server is a fallback for clusters that have no
native RWX StorageClass. When a deployer sets sharedStorage.storageClass
explicitly, they have their own RWX class (Longhorn, EFS, Filestore…) so
the chart's NFS pod is redundant — silently disable it.

Returns "true" / "false" (strings) so callers gate via `eq ... "true"`.
*/}}
{{- define "nebari-data-science-pack.nfsServerEnabled" -}}
{{- if .Values.sharedStorage.storageClass -}}
false
{{- else if .Values.sharedStorage.nfsServer.enabled -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
============================================================================
Zero-config derivation helpers
============================================================================
The deployer-visible API is two fields:
  keycloak.hostname       — drives subdomain inference for hub + nebi
  nebi.image.tag          — pinned per chart release (CI-bumped)
Everything else has a chart-side default; explicit values still win.
*/}}

{{/*
Base DNS domain extracted from keycloak.hostname by stripping the leading
subdomain label. e.g. "keycloak.example.com" -> "example.com". Empty when
keycloak.hostname is unset or has no dot — callers fall back to explicit
nebariapp.hostname / nebi.remoteURL.
*/}}
{{- define "nebari-data-science-pack.baseDomain" -}}
{{- $kc := .Values.keycloak.hostname | default "" -}}
{{- if and $kc (contains "." $kc) -}}
{{- $parts := splitList "." $kc -}}
{{- join "." (rest $parts) -}}
{{- end -}}
{{- end -}}

{{/*
External hub hostname. Order of precedence:
  1. .Values.nebariapp.hostname (explicit)
  2. <subdomains.hub>.<baseDomain(keycloak.hostname)>
Empty when neither is available.
*/}}
{{- define "nebari-data-science-pack.hubHostname" -}}
{{- if .Values.nebariapp.hostname -}}
{{- .Values.nebariapp.hostname -}}
{{- else -}}
{{- $base := include "nebari-data-science-pack.baseDomain" . -}}
{{- if $base -}}
{{- printf "%s.%s" (.Values.subdomains.hub | default "hub") $base -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
External Nebi URL. Order of precedence:
  1. .Values.nebi.remoteURL (explicit)
  2. https://<subdomains.nebi>.<baseDomain(keycloak.hostname)>
Empty when neither is available.
*/}}
{{- define "nebari-data-science-pack.nebiRemoteURL" -}}
{{- if .Values.nebi.remoteURL -}}
{{- .Values.nebi.remoteURL -}}
{{- else -}}
{{- $base := include "nebari-data-science-pack.baseDomain" . -}}
{{- if $base -}}
{{- printf "https://%s.%s" (.Values.subdomains.nebi | default "nebi") $base -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
In-cluster Nebi URL. Order of precedence:
  1. .Values.nebi.internalURL (explicit)
  2. http://nebi-pack-nebari-nebi-pack.<nebi.namespace>.svc.cluster.local
*/}}
{{- define "nebari-data-science-pack.nebiInternalURL" -}}
{{- if .Values.nebi.internalURL -}}
{{- .Values.nebi.internalURL -}}
{{- else -}}
{{- printf "http://nebi-pack-nebari-nebi-pack.%s.svc.cluster.local" (.Values.nebi.namespace | default "nebi") -}}
{{- end -}}
{{- end -}}

{{/*
Nebi image reference (repository:tag). Empty when nebi.image.tag is not
pinned — Python init-container code path stays a no-op.
*/}}
{{- define "nebari-data-science-pack.nebiImage" -}}
{{- $repo := .Values.nebi.image.repository | default "quay.io/nebari/nebi" -}}
{{- $tag := .Values.nebi.image.tag | default "" -}}
{{- if $tag -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}
{{- end -}}

{{/*
Keycloak token endpoint. Order of precedence:
  1. .Values.jupyterhub.custom.keycloak-token-url (explicit)
  2. https://<keycloak.hostname>/realms/<realm>/protocol/openid-connect/token
  3. http://<keycloak.serviceHost>/realms/<realm>/...  (in-cluster default)
The in-cluster default matches the bitnami/keycloakx layout that every
Nebari deployment ships with.
*/}}
{{- define "nebari-data-science-pack.keycloakTokenURL" -}}
{{- $explicit := index .Values.jupyterhub.custom "keycloak-token-url" | default "" -}}
{{- $realm := .Values.keycloak.realm | default "nebari" -}}
{{- if $explicit -}}
{{- $explicit -}}
{{- else if .Values.keycloak.hostname -}}
{{- printf "https://%s/realms/%s/protocol/openid-connect/token" .Values.keycloak.hostname $realm -}}
{{- else -}}
{{- printf "http://%s/realms/%s/protocol/openid-connect/token" (.Values.keycloak.serviceHost | default "keycloak-keycloakx-http.keycloak.svc.cluster.local:8080") $realm -}}
{{- end -}}
{{- end -}}

{{/*
OIDC client IDs follow nebari-operator's naming convention:
  jupyterhub-<release>-<chart>  (hub's own client)
  nebi-<release>-<chart>        (nebi service client, provisioned by nebi-pack)
*/}}
{{- define "nebari-data-science-pack.hubClientID" -}}
{{- $explicit := index .Values.jupyterhub.custom "jupyterhub-client-id" | default "" -}}
{{- if $explicit -}}
{{- $explicit -}}
{{- else -}}
{{- printf "jupyterhub-%s" (include "nebari-data-science-pack.fullname" .) -}}
{{- end -}}
{{- end -}}

{{- define "nebari-data-science-pack.nebiClientID" -}}
{{- $explicit := index .Values.jupyterhub.custom "nebi-client-id" | default "" -}}
{{- if $explicit -}}
{{- $explicit -}}
{{- else -}}
{{- printf "nebi-%s-nebari-nebi-pack" (.Values.nebi.releaseName | default "nebi-pack") -}}
{{- end -}}
{{- end -}}

{{/*
External JupyterHub URL with scheme. Used for OAuth callback / bind_url.
Empty when no hub hostname is available.
*/}}
{{- define "nebari-data-science-pack.hubExternalURL" -}}
{{- $h := include "nebari-data-science-pack.hubHostname" . -}}
{{- if $h -}}
{{- printf "https://%s/" $h -}}
{{- end -}}
{{- end -}}
