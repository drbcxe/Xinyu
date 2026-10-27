CREATE FUNCTION public.xinyu_storage_owner(value jsonb) RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE
 WHEN (value->>'owner') ~ '^[a-f0-9]{64}$' THEN value->>'owner'
 WHEN (value->>'scope') ~ '^[a-f0-9]{64}$' THEN value->>'scope'
 WHEN (value->>'thread_id') ~ '^xinyu:[a-f0-9]{64}:' THEN split_part(value->>'thread_id',':',2)
 ELSE NULL END
$$;

CREATE FUNCTION public.xinyu_storage_category(schema_name text, table_name text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
 SELECT CASE WHEN schema_name='xinyu_graph' THEN 'graph'
 WHEN table_name='relationship_items' THEN 'items'
 WHEN table_name IN ('relationship_turns','relationship_feedback') THEN 'conversations'
 WHEN table_name='relationship_jobs' THEN 'jobs'
 ELSE 'metadata' END
$$;

CREATE FUNCTION public.xinyu_storage_adjust(owner_key text, category_key text, amount bigint) RETURNS void LANGUAGE plpgsql AS $$
BEGIN
 IF amount=0 THEN RETURN; END IF;
 INSERT INTO public.relationship_storage_totals(scope,category,bytes) VALUES('global',category_key,GREATEST(0,amount))
 ON CONFLICT(scope,category) DO UPDATE SET bytes=GREATEST(0,relationship_storage_totals.bytes+amount);
 IF owner_key IS NOT NULL THEN
   INSERT INTO public.relationship_storage_totals(scope,category,bytes) VALUES(owner_key,category_key,GREATEST(0,amount))
   ON CONFLICT(scope,category) DO UPDATE SET bytes=GREATEST(0,relationship_storage_totals.bytes+amount);
 END IF;
 DELETE FROM public.relationship_storage_totals WHERE category=category_key AND bytes=0 AND (scope='global' OR scope=owner_key);
END $$;

CREATE FUNCTION public.xinyu_storage_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
 before_value jsonb; after_value jsonb;
 old_owner text; new_owner text; category_key text;
 old_bytes bigint:=0; new_bytes bigint:=0; delta bigint;
 policy public.relationship_storage_policy%ROWTYPE;
 old_active boolean:=false; new_active boolean:=false;
 job_key text; available bigint; debit bigint; change_reserved bigint:=0;
 thread_key text; thread_bytes bigint; total_bytes bigint; allowed bigint;
 essential boolean:=false;
BEGIN
 PERFORM pg_advisory_xact_lock(718302612);
 IF TG_OP<>'INSERT' THEN before_value:=to_jsonb(OLD); old_owner:=public.xinyu_storage_owner(before_value); old_bytes:=octet_length(before_value::text); END IF;
 IF TG_OP<>'DELETE' THEN after_value:=to_jsonb(NEW); new_owner:=public.xinyu_storage_owner(after_value); new_bytes:=octet_length(after_value::text); END IF;
 category_key:=public.xinyu_storage_category(TG_TABLE_SCHEMA,TG_TABLE_NAME);
 SELECT * INTO policy FROM public.relationship_storage_policy WHERE id=1;
 IF TG_TABLE_NAME='relationship_jobs' THEN
   old_active:=COALESCE(before_value->>'status','') IN ('queued','running');
   new_active:=COALESCE(after_value->>'status','') IN ('queued','running');
   job_key:=COALESCE(after_value->>'id',before_value->>'id');
   IF NOT old_active AND new_active THEN
     INSERT INTO public.relationship_storage_reservations(job_id,owner,remaining) VALUES(job_key,new_owner,COALESCE(policy.job_reserve_bytes,6291456)) ON CONFLICT DO NOTHING;
     IF FOUND THEN change_reserved:=COALESCE(policy.job_reserve_bytes,6291456); END IF;
   ELSIF old_active AND NOT new_active THEN
     DELETE FROM public.relationship_storage_reservations WHERE job_id=job_key RETURNING remaining INTO available;
     change_reserved:=-COALESCE(available,0);
   END IF;
   PERFORM public.xinyu_storage_adjust(COALESCE(new_owner,old_owner),'reserved',change_reserved);
   essential:=old_active AND NOT new_active;
 END IF;
 IF TG_TABLE_SCHEMA='xinyu_graph' THEN
   thread_key:=COALESCE(after_value->>'thread_id',before_value->>'thread_id');
   job_key:=split_part(thread_key,':',3);
   INSERT INTO public.relationship_storage_threads(thread_id,owner,bytes) VALUES(thread_key,COALESCE(new_owner,old_owner),GREATEST(0,new_bytes-old_bytes))
   ON CONFLICT(thread_id) DO UPDATE SET bytes=GREATEST(0,relationship_storage_threads.bytes+new_bytes-old_bytes) RETURNING bytes INTO thread_bytes;
   IF thread_bytes=0 THEN DELETE FROM public.relationship_storage_threads WHERE thread_id=thread_key; END IF;
   IF new_bytes>old_bytes AND thread_bytes>COALESCE(policy.graph_thread_bytes,4194304) AND COALESCE(current_setting('xinyu.storage_bypass',true),'')<>'on' THEN
     RAISE EXCEPTION USING ERRCODE='XY003',MESSAGE='GRAPH_STORAGE_LIMIT';
   END IF;
 ELSIF TG_TABLE_NAME='relationship_turns' AND new_bytes>old_bytes THEN
   SELECT id INTO job_key FROM public.relationship_jobs WHERE owner=new_owner AND request_id=after_value->>'request_id' AND status IN ('queued','running') LIMIT 1;
 ELSIF TG_TABLE_NAME='relationship_model_calls' AND new_bytes>old_bytes THEN
   SELECT id INTO job_key FROM public.relationship_jobs WHERE owner=new_owner AND request_id=after_value->>'operation_id' AND status IN ('queued','running') LIMIT 1;
 END IF;
 delta:=new_bytes-old_bytes;
 IF delta>0 AND job_key IS NOT NULL THEN
   SELECT remaining INTO available FROM public.relationship_storage_reservations WHERE job_id=job_key;
   debit:=LEAST(COALESCE(available,0),delta);
   IF debit>0 THEN
     UPDATE public.relationship_storage_reservations SET remaining=remaining-debit WHERE job_id=job_key;
     PERFORM public.xinyu_storage_adjust(COALESCE(new_owner,old_owner),'reserved',-debit);
     change_reserved:=change_reserved-debit;
   END IF;
 ELSIF category_key='graph' AND delta<0 AND job_key IS NOT NULL THEN
   SELECT remaining INTO available FROM public.relationship_storage_reservations WHERE job_id=job_key;
   debit:=LEAST(COALESCE(policy.job_reserve_bytes,6291456)-COALESCE(available,0),-delta);
   IF available IS NOT NULL AND debit>0 THEN
     UPDATE public.relationship_storage_reservations SET remaining=remaining+debit WHERE job_id=job_key;
     PERFORM public.xinyu_storage_adjust(COALESCE(new_owner,old_owner),'reserved',debit);
     change_reserved:=change_reserved+debit;
   END IF;
 END IF;
 IF old_owner IS NOT DISTINCT FROM new_owner THEN
   PERFORM public.xinyu_storage_adjust(COALESCE(new_owner,old_owner),category_key,delta);
 ELSE
   PERFORM public.xinyu_storage_adjust(old_owner,category_key,-old_bytes);
   PERFORM public.xinyu_storage_adjust(new_owner,category_key,new_bytes);
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 IF COALESCE(current_setting('xinyu.storage_bypass',true),'')='on' THEN RETURN NEW; END IF;
 -- Shrinking writes and equal-size lease/status updates must remain possible at/above a reduced cap.
 IF delta+change_reserved<=0 THEN RETURN NEW; END IF;
 IF policy.id IS NULL THEN RAISE EXCEPTION USING ERRCODE='XY004',MESSAGE='STORAGE_POLICY_MISSING'; END IF;
 essential:=essential OR TG_TABLE_NAME='relationship_sessions'
 OR (TG_TABLE_NAME='relationship_model_calls' AND before_value->>'status'='reserved' AND after_value->>'status'<>'reserved')
 OR (TG_TABLE_NAME='relationship_rag_indexes' AND after_value->>'status' IN ('deleting','delete_failed','failed','ready'))
 OR (TG_TABLE_NAME='relationship_daily_usage' AND TG_OP='UPDATE')
 OR (TG_TABLE_NAME='relationship_privacy' AND after_value->>'revoked_at' IS NOT NULL);
 IF TG_TABLE_NAME='relationship_profiles' AND TG_OP='INSERT' AND (SELECT count(*) FROM public.relationship_profiles)>policy.profile_limit THEN
   RAISE EXCEPTION USING ERRCODE='XY005',MESSAGE='SPACE_COUNT_LIMIT';
 END IF;
 SELECT COALESCE(sum(bytes),0) INTO total_bytes FROM public.relationship_storage_totals WHERE scope='global';
 allowed:=policy.global_bytes-CASE WHEN essential THEN 0 ELSE LEAST(policy.emergency_bytes*128,policy.global_bytes/20) END;
 IF total_bytes>allowed THEN RAISE EXCEPTION USING ERRCODE='XY002',MESSAGE='GLOBAL_STORAGE_QUOTA_EXCEEDED'; END IF;
 IF new_owner IS NOT NULL THEN
   SELECT COALESCE(sum(bytes),0) INTO total_bytes FROM public.relationship_storage_totals WHERE scope=new_owner;
   allowed:=policy.owner_bytes-CASE WHEN essential THEN 0 ELSE LEAST(policy.emergency_bytes,policy.owner_bytes/20) END;
   IF total_bytes>allowed THEN RAISE EXCEPTION USING ERRCODE='XY001',MESSAGE='SPACE_QUOTA_EXCEEDED'; END IF;
 END IF;
 RETURN NEW;
END $$;

-- Acquire the accounting lock before DML acquires row locks (also applies to raw psycopg writes).
CREATE FUNCTION public.xinyu_storage_stmt_lock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(718302612);
 RETURN NULL;
END $$;

CREATE FUNCTION public.xinyu_storage_install() RETURNS integer LANGUAGE plpgsql AS $$
DECLARE r record; installed integer:=0;
BEGIN
 PERFORM pg_advisory_xact_lock(718302612);
 FOR r IN SELECT schemaname,tablename FROM pg_tables WHERE
 (schemaname='public' AND tablename LIKE 'relationship_%' AND tablename NOT LIKE 'relationship_storage_%')
 OR (schemaname='xinyu_graph' AND tablename IN ('checkpoints','checkpoint_blobs','checkpoint_writes')) LOOP
   IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=format('%I.%I',r.schemaname,r.tablename)::regclass AND tgname='xinyu_storage_lock') THEN
     EXECUTE format('CREATE TRIGGER xinyu_storage_lock BEFORE INSERT OR UPDATE OR DELETE ON %I.%I FOR EACH STATEMENT EXECUTE FUNCTION public.xinyu_storage_stmt_lock()',r.schemaname,r.tablename);
     installed:=installed+1;
   END IF;
   IF NOT EXISTS(SELECT 1 FROM pg_trigger WHERE tgrelid=format('%I.%I',r.schemaname,r.tablename)::regclass AND tgname='xinyu_storage_accounting') THEN
     EXECUTE format('CREATE TRIGGER xinyu_storage_accounting AFTER INSERT OR UPDATE OR DELETE ON %I.%I FOR EACH ROW EXECUTE FUNCTION public.xinyu_storage_row()',r.schemaname,r.tablename);
     installed:=installed+1;
   END IF;
 END LOOP;
 RETURN installed;
END $$;

CREATE FUNCTION public.xinyu_storage_rebuild() RETURNS void LANGUAGE plpgsql AS $$
DECLARE r record; payload record; owner_key text; category_key text; amount bigint;
BEGIN
 PERFORM pg_advisory_xact_lock(718302612);
 DELETE FROM public.relationship_storage_totals;
 DELETE FROM public.relationship_storage_threads;
 DELETE FROM public.relationship_storage_reservations reservation WHERE NOT EXISTS (
   SELECT 1 FROM public.relationship_jobs j WHERE j.id=reservation.job_id AND j.owner=reservation.owner AND j.status IN ('queued','running'));
 FOR r IN SELECT schemaname,tablename FROM pg_tables WHERE
 (schemaname='public' AND tablename LIKE 'relationship_%' AND tablename NOT LIKE 'relationship_storage_%')
 OR (schemaname='xinyu_graph' AND tablename IN ('checkpoints','checkpoint_blobs','checkpoint_writes')) LOOP
   category_key:=public.xinyu_storage_category(r.schemaname,r.tablename);
   FOR payload IN EXECUTE format('SELECT to_jsonb(t) AS value FROM %I.%I t',r.schemaname,r.tablename) LOOP
     owner_key:=public.xinyu_storage_owner(payload.value);amount:=octet_length(payload.value::text);
     PERFORM public.xinyu_storage_adjust(owner_key,category_key,amount);
     IF r.schemaname='xinyu_graph' THEN
       INSERT INTO public.relationship_storage_threads(thread_id,owner,bytes) VALUES(payload.value->>'thread_id',owner_key,amount)
       ON CONFLICT(thread_id) DO UPDATE SET bytes=relationship_storage_threads.bytes+amount;
     END IF;
   END LOOP;
 END LOOP;
 INSERT INTO public.relationship_storage_reservations(job_id,owner,remaining)
 SELECT j.id,j.owner,GREATEST(0,p.job_reserve_bytes-COALESCE(t.bytes,0)-octet_length(to_jsonb(j)::text)
   -COALESCE((SELECT sum(octet_length(to_jsonb(c)::text)) FROM public.relationship_model_calls c
     WHERE c.owner=j.owner AND c.operation_id=j.request_id),0)) FROM public.relationship_jobs j
 CROSS JOIN public.relationship_storage_policy p LEFT JOIN public.relationship_storage_threads t ON t.thread_id='xinyu:'||j.owner||':'||j.id
 WHERE j.status IN ('queued','running') AND p.id=1 ON CONFLICT(job_id) DO NOTHING;
 FOR payload IN SELECT owner,remaining FROM public.relationship_storage_reservations LOOP
   PERFORM public.xinyu_storage_adjust(payload.owner,'reserved',payload.remaining);
 END LOOP;
END $$;
