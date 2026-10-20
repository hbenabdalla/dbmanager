{{/* Names */}}
{{- define "dbm.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "dbm.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "dbm.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/name: {{ include "dbm.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: dbmanager
{{- end -}}

{{/* selector labels: include "dbm.selector" (dict "ctx" $ "component" "api") */}}
{{- define "dbm.selector" -}}
app.kubernetes.io/name: {{ include "dbm.name" .ctx }}
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "dbm.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "dbm.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "dbm.secretName" -}}
{{- default (printf "%s-secrets" (include "dbm.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}

{{/* image reference: include "dbm.image" (dict "ctx" $ "img" .Values.image.backend) */}}
{{- define "dbm.image" -}}
{{- if .img.digest -}}
{{- printf "%s@%s" .img.repository .img.digest -}}
{{- else -}}
{{- printf "%s:%s" .img.repository (default .ctx.Chart.AppVersion .img.tag) -}}
{{- end -}}
{{- end -}}

{{/* Pod security context (restricted Pod Security Standard). uid/gid are pinned outside OpenShift. */}}
{{- define "dbm.podSecurity" -}}
runAsNonRoot: true
seccompProfile: { type: RuntimeDefault }
{{- if not .ctx.Values.openshift }}
runAsUser: {{ .uid }}
runAsGroup: {{ default .uid .gid }}
fsGroup: {{ default .uid .gid }}
{{- end }}
{{- end -}}

{{- define "dbm.containerSecurity" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
capabilities: { drop: [ALL] }
{{- end -}}

{{- define "dbm.timeseriesUrl" -}}
{{- if .Values.timeseries.bundled.enabled -}}
{{- printf "http://%s-timeseries:8428" (include "dbm.fullname" .) -}}
{{- else -}}
{{- required "timeseries.external.url is required when timeseries.bundled.enabled=false" .Values.timeseries.external.url -}}
{{- end -}}
{{- end -}}

{{/* environment shared by API, worker and the test hook */}}
{{- define "dbm.backendEnv" -}}
- name: DBM_DATABASE_URL
  valueFrom: { secretKeyRef: { name: {{ include "dbm.secretName" . }}, key: DBM_DATABASE_URL } }
- name: DBM_MASTER_KEY
  valueFrom: { secretKeyRef: { name: {{ include "dbm.secretName" . }}, key: DBM_MASTER_KEY } }
- name: DBM_BOOTSTRAP_ADMIN_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ include "dbm.secretName" . }}, key: DBM_BOOTSTRAP_ADMIN_PASSWORD, optional: true } }
- name: DBM_TIMESERIES_PASSWORD
  valueFrom: { secretKeyRef: { name: {{ include "dbm.secretName" . }}, key: DBM_TIMESERIES_PASSWORD } }
{{- if .Values.postgresql.tls.caSecret.name }}
- { name: DBM_DATABASE_TLS_CA_FILE, value: /etc/dbmanager/tls/postgresql/ca.crt }
{{- end }}
{{- if .Values.timeseries.tls.caSecret.name }}
- { name: DBM_TIMESERIES_TLS_CA_FILE, value: /etc/dbmanager/tls/timeseries/ca.crt }
{{- end }}
{{- end -}}

{{- define "dbm.backendVolumeMounts" -}}
- { name: tmp, mountPath: /tmp }
- { name: work, mountPath: /var/lib/dbmanager/work }
- { name: backups, mountPath: /var/lib/dbmanager/backups }
{{- if .Values.postgresql.tls.caSecret.name }}
- { name: pg-ca, mountPath: /etc/dbmanager/tls/postgresql, readOnly: true }
{{- end }}
{{- if .Values.timeseries.tls.caSecret.name }}
- { name: ts-ca, mountPath: /etc/dbmanager/tls/timeseries, readOnly: true }
{{- end }}
{{- end -}}

{{- define "dbm.backendVolumes" -}}
- { name: tmp, emptyDir: {} }
- { name: work, emptyDir: {} }
{{- if .Values.backupStorage.persistence.enabled }}
- name: backups
  persistentVolumeClaim: { claimName: {{ include "dbm.fullname" . }}-backups }
{{- else }}
- { name: backups, emptyDir: {} }
{{- end }}
{{- with .Values.postgresql.tls.caSecret }}
{{- if .name }}
- name: pg-ca
  secret: { secretName: {{ .name }}, items: [{ key: {{ .key }}, path: ca.crt }] }
{{- end }}
{{- end }}
{{- with .Values.timeseries.tls.caSecret }}
{{- if .name }}
- name: ts-ca
  secret: { secretName: {{ .name }}, items: [{ key: {{ .key }}, path: ca.crt }] }
{{- end }}
{{- end }}
{{- end -}}

{{/* checksum of the chart-managed config, so pods roll when it changes */}}
{{- define "dbm.configChecksum" -}}
{{- include (print .Template.BasePath "/configmap.yaml") . | sha256sum -}}
{{- end -}}

{{- define "dbm.spread" -}}
{{- if .enabled }}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: ScheduleAnyway
    labelSelector:
      matchLabels:
        {{- include "dbm.selector" (dict "ctx" .ctx "component" .component) | nindent 8 }}
{{- end }}
{{- end -}}
