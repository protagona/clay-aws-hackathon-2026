#!/usr/bin/env bash
# A-2: copy a tiny NAIP and Vantor sample into s3://<RunsBucket>/samples/, a cost-safe fallback to reading at the
# source. Run after `cdk deploy`, with credentials for the same account:
#
#   AWS_PROFILE=<profile> scripts/stage_samples.sh
#
# Server-side copies in us-west-2 (~85 MB in total): NAIP is Requester Pays, so this account pays a few requests;
# the Vantor bucket is public. Sources and licenses are written to samples/README.txt next to the files.
set -euo pipefail

STACK=ClayFoundryEvalSandbox
REGION=${AWS_REGION:-us-west-2}
BUCKET=$(aws cloudformation describe-stacks --stack-name "$STACK" --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='RunsBucketName'].OutputValue" --output text)
[ -n "$BUCKET" ] && [ "$BUCKET" != "None" ] || { echo "No RunsBucketName output on stack $STACK; deploy first" >&2; exit 2; }

# NAIP 2022, 60 cm RGB COG, north of Fresno, CA (54 MB). Same scene as Earth Search item ca_m_3611919_nw_11_060_20220702.
NAIP_SRC=s3://naip-visualization/ca/2022/60cm/rgb/36119/m_3611919_nw_11_060_20220702.tif
# Vantor open data, Nepal flooding, Aug 2026 (30 MB).
VANTOR_SRC=s3://vantor-opendata/events/Nepal-Flooding-Aug-2026/B120001101250A10.tif

aws s3 cp "$NAIP_SRC" "s3://$BUCKET/samples/naip/$(basename "$NAIP_SRC")" --region "$REGION" --request-payer requester \
  --copy-props metadata-directive  # public source buckets refuse GetObjectTagging
aws s3 cp "$VANTOR_SRC" "s3://$BUCKET/samples/vantor/$(basename "$VANTOR_SRC")" --region "$REGION" \
  --copy-props metadata-directive

cat <<EOF | aws s3 cp - "s3://$BUCKET/samples/README.txt" --region "$REGION"
Pre-staged imagery samples for the Foundry eval sandbox (backlog A-2). Read-only for the harness role.

samples/naip/$(basename "$NAIP_SRC")
  source:  $NAIP_SRC (Requester Pays)
  what:    USDA NAIP 2022, 60 cm, RGB COG, Fresno County CA
  license: public domain (USDA FSA)

samples/vantor/$(basename "$VANTOR_SRC")
  source:  $VANTOR_SRC
  what:    Vantor (formerly Maxar) open data, Nepal flooding, August 2026
  license: CC BY-NC 4.0 (s3://vantor-opendata/LICENSE.TXT); non-commercial use with attribution
EOF

aws s3 ls "s3://$BUCKET/samples/" --recursive --region "$REGION"
