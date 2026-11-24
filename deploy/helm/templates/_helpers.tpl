{{- define "opencloudops.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "opencloudops.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "opencloudops.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "opencloudops.labels" -}}
app.kubernetes.io/name: {{ include "opencloudops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "opencloudops.selectorLabels" -}}
app.kubernetes.io/name: {{ include "opencloudops.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "opencloudops.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "opencloudops.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}
