"""명시적 사용자 확인 후 저장/프로필 불명 표식만 해제하는 대화형 도구."""
import argparse
import hashlib
from pathlib import Path

import config
import pipeline


def main():
    parser = argparse.ArgumentParser(description='네이버에서 저장 결과와 브라우저 종료를 직접 확인한 후 불명 표식을 해제합니다.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--draft', type=Path, help='결과를 확인한 원본 Markdown 경로')
    group.add_argument('--account-id', help='브라우저 종료를 확인한 내부 계정 ID')
    group.add_argument('--list-accounts', action='store_true', help='계정 표시 이름과 내부 ID 조회')
    args = parser.parse_args()
    if args.list_accounts:
        for account in pipeline.load_accounts():
            print(f"{account['label']}: {account['id']}")
        return
    if args.draft:
        draft = args.draft.resolve()
        marker = draft.parent / ('.save-intent-' + hashlib.sha256(str(draft).encode('utf-8')).hexdigest() + '.json')
        if not marker.is_file() or marker.read_text(encoding='utf-8') != 'save outcome uncertain\n':
            raise SystemExit('해당 초안에 유효한 불명 표식이 없습니다.')
        prompt = '네이버에서 해당 초안의 저장 결과를 직접 확인했습니까? YES 입력: '
    else:
        session = config.publisher_session_file(args.account_id)
        marker = session.parent.parent / ('.flush-unknown-' + session.parent.name)
        if not marker.is_file() or marker.stat().st_size != 0:
            raise SystemExit('해당 계정에 유효한 불명 표식이 없습니다.')
        prompt = '해당 작업의 브라우저가 모두 종료되었는지 직접 확인했습니까? YES 입력: '
    if input(prompt) != 'YES':
        raise SystemExit('표식을 유지했습니다.')
    marker.unlink()
    print('불명 표식을 해제했습니다. 다음 실행에서 세션을 다시 확인합니다.')


if __name__ == '__main__':
    main()
