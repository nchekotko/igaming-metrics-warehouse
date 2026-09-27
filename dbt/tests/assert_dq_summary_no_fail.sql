-- Any DQ check in 'fail' status fails the build.
select * from {{ ref('dq_summary') }} where status = 'fail'
