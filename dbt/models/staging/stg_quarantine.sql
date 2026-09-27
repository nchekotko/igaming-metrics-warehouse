select entity, partition, line_no, source, ingested_at, raw_line,
       errors, json_extract_string(errors, '$[0]') as first_error, _batch_id
from {{ source('raw', 'quarantine') }}
