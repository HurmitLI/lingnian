#!/usr/bin/env python3
"""Check the formal queue before a planned API release; default is read-only.

This is an operational preflight, not a distributed lock. Run planned releases
in a maintenance window with new submissions paused by the operator.
"""
import argparse
import json
import subprocess


QUERY = """
import json
from sqlalchemy import select,func,or_,and_
from app.core.database import SessionLocal
from app.models import GenerativeMediaRequest,WorkflowTask,ShortSceneJob,ShortSceneReferenceJob
with SessionLocal() as db:
    counts={
        'generation':db.scalar(select(func.count()).select_from(GenerativeMediaRequest).where(or_(
            GenerativeMediaRequest.status.in_(['queued','processing']),
            and_(GenerativeMediaRequest.status=='failed',GenerativeMediaRequest.error_code=='GENERATION_LEASE_EXPIRED')))),
        'text_audio_story':db.scalar(select(func.count()).select_from(WorkflowTask).where(WorkflowTask.status.in_(['queued','running']))),
        'short_video':db.scalar(select(func.count()).select_from(ShortSceneJob).where(or_(ShortSceneJob.status.in_(['queued','preparing','generating','interrupted']),and_(ShortSceneJob.status=='failed',ShortSceneJob.error_code=='OUTCOME_UNKNOWN')))),
        'reference_image':db.scalar(select(func.count()).select_from(ShortSceneReferenceJob).where(or_(ShortSceneReferenceJob.status.in_(['queued','preparing','generating','interrupted']),and_(ShortSceneReferenceJob.status=='failed',ShortSceneReferenceJob.error_code=='OUTCOME_UNKNOWN')))),
    }
print('LINGNIAN_RELEASE_PREFLIGHT:'+json.dumps(counts))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', default='ukr0m5bp', help='Exact formal API function ID')
    parser.add_argument('--release', action='store_true', help='Release only after an idle preflight')
    args = parser.parse_args()
    result = subprocess.run(['vefaas','fn','exec','--id',args.id,'--yes','--','python3','-c',QUERY],
                            capture_output=True,text=True)
    if result.returncode:
        print('无法确认线上任务状态，未发布。')
        return 2
    rows=[line for line in result.stdout.splitlines() if line.startswith('LINGNIAN_RELEASE_PREFLIGHT:')]
    if len(rows)!=1:
        print('线上检查结果不完整，未发布。')
        return 2
    counts=json.loads(rows[0].split(':',1)[1])
    print(json.dumps(counts,ensure_ascii=False))
    if any(counts.values()):
        print('仍有排队、执行或待核对任务，未发布。先完成原任务，避免滚动更新丢失在途结果。')
        return 2
    if not args.release:
        print('当前队列空闲。本次只检查，未发布；仍需在没有新提交的维护窗口执行发布。')
        return 0
    return subprocess.run(['vefaas','fn','release','--id',args.id,'--yes']).returncode


if __name__=='__main__':
    raise SystemExit(main())
