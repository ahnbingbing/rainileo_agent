#!/usr/bin/env bash
# deploy/provision_gcp.sh — provision the VM brain on GCP (project rianileo-veo).
# Split into FREE/reversible prep (APIs, service account, IAM) and the BILLABLE VM
# create, which is guarded so nothing bills by accident.
#
#   bash deploy/provision_gcp.sh prep         # free: enable APIs + SA + IAM roles
#   bash deploy/provision_gcp.sh vm           # BILLABLE: create the e2-medium VM (asks to confirm)
#   bash deploy/provision_gcp.sh ssh          # ssh into the VM
#
# The secret (your .env) is NOT handled here — you create it yourself so credentials
# never pass through any tool transcript:
#   gcloud secrets create rianileo-env --replication-policy=automatic --project=rianileo-veo
#   gcloud secrets versions add rianileo-env --data-file=.env --project=rianileo-veo
set -euo pipefail

PROJECT="${PROJECT:-rianileo-veo}"
REGION="${REGION:-asia-northeast3}"
ZONE="${ZONE:-asia-northeast3-a}"
VM="${VM:-rianileo-brain}"
SA="${SA:-rianileo-vm}"
SA_EMAIL="$SA@$PROJECT.iam.gserviceaccount.com"
BUCKET="${BUCKET:-rianileo-assets}"
DISK_GB="${DISK_GB:-50}"
G="gcloud --project=$PROJECT"

prep() {
  echo "== enable APIs (free) =="
  $G services enable compute.googleapis.com secretmanager.googleapis.com aiplatform.googleapis.com

  echo "== service account (free) =="
  $G iam service-accounts describe "$SA_EMAIL" >/dev/null 2>&1 || \
    $G iam service-accounts create "$SA" --display-name="Rianileo VM brain"

  echo "== IAM roles (free) =="
  # read the env secret
  $G projects add-iam-policy-binding "$PROJECT" \
     --member="serviceAccount:$SA_EMAIL" --role=roles/secretmanager.secretAccessor --condition=None -q >/dev/null
  # Veo via Vertex
  $G projects add-iam-policy-binding "$PROJECT" \
     --member="serviceAccount:$SA_EMAIL" --role=roles/aiplatform.user --condition=None -q >/dev/null
  # read/write the asset bucket only (scoped, not project-wide storage)
  gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
     --member="serviceAccount:$SA_EMAIL" --role=roles/storage.objectAdmin >/dev/null
  echo "   SA=$SA_EMAIL granted: secretAccessor, aiplatform.user, objectAdmin@$BUCKET"
  echo "PREP DONE. Next: create the secret (you, manually), then: bash $0 vm"
}

vm() {
  if ! $G secrets describe rianileo-env >/dev/null 2>&1; then
    echo "!! secret 'rianileo-env' not found — create it FIRST (see header), then re-run."; exit 1
  fi
  echo "== about to CREATE A BILLABLE VM =="
  echo "   $VM  e2-medium  $ZONE  debian-12  ${DISK_GB}GB  SA=$SA_EMAIL  (~\$25-35/mo)"
  read -r -p "   type 'yes' to create: " ans
  [ "$ans" = "yes" ] || { echo "aborted."; exit 0; }
  $G compute instances create "$VM" \
    --zone="$ZONE" --machine-type=e2-medium \
    --image-family=debian-12 --image-project=debian-cloud \
    --boot-disk-size="${DISK_GB}GB" --boot-disk-type=pd-balanced \
    --service-account="$SA_EMAIL" --scopes=cloud-platform \
    --labels=app=rianileo,role=brain
  echo "VM created. Next: bash $0 ssh   then on the VM run deploy/bootstrap.sh"
}

ssh_() { $G compute ssh "$VM" --zone="$ZONE"; }

case "${1:-}" in
  prep) prep ;;
  vm)   vm ;;
  ssh)  ssh_ ;;
  *)    echo "usage: $0 {prep|vm|ssh}"; exit 2 ;;
esac
