*** Settings ***
Documentation     ETSI GS CIM 009 V1.9.1 temporal read suite (EP-30, GW26, TS-19).
...               Asserts temporal query and retrieval against CIM 009 clauses 6.18–6.20.
Resource          resources/ngsi_ld.resource
Suite Setup       Setup Fixture Entities
Suite Teardown    Teardown NGSI-LD Session
Force Tags        etsi    temporal

*** Variables ***
${SETUP_ID_1}     ${EMPTY}
${SETUP_ID_2}     ${EMPTY}
${T1}             ${EMPTY}
${T2}             ${EMPTY}
${T3}             ${EMPTY}
${T1_MINUS_1S}    ${EMPTY}
${T2_PLUS_1S}     ${EMPTY}

*** Keywords ***
Setup Fixture Entities
    Open NGSI-LD Session
    # An endpoint without history answers [] not 500 (assert 200 + empty list before fixture is written)
    ${pre_check}=    Get From SUT    /temporal/entities    type=AirQualityObserved
    Should Be Equal As Integers    ${pre_check.status_code}    200
    ${pre_items}=    Set Variable    ${pre_check.json()}
    Should Be Empty    ${pre_items}

    ${t1}=    Get ISO Timestamp    -1200
    ${t2}=    Get ISO Timestamp    -600
    ${t3}=    Get ISO Timestamp    0
    ${t1_minus}=    Get ISO Timestamp    -1201
    ${t2_plus}=    Get ISO Timestamp    -599
    Set Suite Variable    ${T1}    ${t1}
    Set Suite Variable    ${T2}    ${t2}
    Set Suite Variable    ${T3}    ${t3}
    Set Suite Variable    ${T1_MINUS_1S}    ${t1_minus}
    Set Suite Variable    ${T2_PLUS_1S}    ${t2_plus}

    ${id1}=    Random Entity Id    AirQualityObserved
    ${id2}=    Random Entity Id    AirQualityObserved
    Set Suite Variable    ${SETUP_ID_1}    ${id1}
    Set Suite Variable    ${SETUP_ID_2}    ${id2}

    ${e1}=    Build AirQuality Entity    ${id1}    temp=18.0    humidity=45.0    name=Stanica Štadión
    ${e2}=    Build AirQuality Entity    ${id2}    temp=22.0    humidity=60.0    name=Stanica Sásová
    ${r1}=    Create Entity In SUT    ${e1}
    Should Be Equal As Integers    ${r1.status_code}    201
    ${r2}=    Create Entity In SUT    ${e2}
    Should Be Equal As Integers    ${r2.status_code}    201

    ${obs1_1}=    Build Temporal Observation Payload    19.0    48.0    ${t1}
    ${obs1_2}=    Build Temporal Observation Payload    20.5    52.0    ${t2}
    ${obs1_3}=    Build Temporal Observation Payload    21.0    55.0    ${t3}
    ${ap1_1}=    Append Entity Attributes    ${id1}    ${obs1_1}
    Should Contain    ${{ [200, 204] }}    ${ap1_1.status_code}
    ${ap1_2}=    Append Entity Attributes    ${id1}    ${obs1_2}
    Should Contain    ${{ [200, 204] }}    ${ap1_2.status_code}
    ${ap1_3}=    Append Entity Attributes    ${id1}    ${obs1_3}
    Should Contain    ${{ [200, 204] }}    ${ap1_3.status_code}

    ${obs2_1}=    Build Temporal Observation Payload    23.0    62.0    ${t1}
    ${obs2_2}=    Build Temporal Observation Payload    24.5    65.0    ${t2}
    ${obs2_3}=    Build Temporal Observation Payload    25.0    68.0    ${t3}
    ${ap2_1}=    Append Entity Attributes    ${id2}    ${obs2_1}
    Should Contain    ${{ [200, 204] }}    ${ap2_1.status_code}
    ${ap2_2}=    Append Entity Attributes    ${id2}    ${obs2_2}
    Should Contain    ${{ [200, 204] }}    ${ap2_2.status_code}
    ${ap2_3}=    Append Entity Attributes    ${id2}    ${obs2_3}
    Should Contain    ${{ [200, 204] }}    ${ap2_3.status_code}

*** Test Cases ***
CIM009 6.18.3.1 Temporal Query Returns Array Of Temporal Instances
    [Documentation]    Clause 6.18.3.1: GET /temporal/entities?type=AirQualityObserved&timerel=after&timeAt=<T1-1s>
    ...                returns 200 with Content-Type application/ld+json (or application/json with Link context).
    ...                Every entity carries the attribute as an ARRAY of temporal instances, each with observedAt
    ...                and unique instanceId.
    ${response}=    Get From SUT    /temporal/entities
    ...    type=AirQualityObserved
    ...    timerel=after
    ...    timeAt=${T1_MINUS_1S}
    Should Be Equal As Integers    ${response.status_code}    200
    ${content_type}=    Get From Dictionary    ${response.headers}    Content-Type
    ${is_ld_json}=    Evaluate    'application/ld+json' in '${content_type}'
    ${is_json}=    Evaluate    'application/json' in '${content_type}'
    Should Be True    ${is_ld_json} or ${is_json}
    IF    ${is_json} and not ${is_ld_json}
        Dictionary Should Contain Key    ${response.headers}    Link
        Should Contain    ${response.headers}[Link]    rel="http://www.w3.org/ns/json-ld#context"
    END
    ${entities}=    Set Variable    ${response.json()}
    Should Not Be Empty    ${entities}
    FOR    ${entity}    IN    @{entities}
        Dictionary Should Contain Key    ${entity}    temperature
        ${instances}=    Set Variable    ${entity}[temperature]
        ${is_list}=    Evaluate    isinstance($instances, list)
        Should Be True    ${is_list}
        ${instance_ids}=    Create List
        FOR    ${inst}    IN    @{instances}
            Dictionary Should Contain Key    ${inst}    observedAt
            Dictionary Should Contain Key    ${inst}    instanceId
            ${iid}=    Set Variable    ${inst}[instanceId]
            List Should Not Contain Value    ${instance_ids}    ${iid}
            Append To List    ${instance_ids}    ${iid}
        END
    END

CIM009 6.18.3.1 Temporal Query Filters By Time Range
    [Documentation]    timerel=before returns instances before timeAt; timerel=between returns instances
    ...                in range and never outside; timerel=after without timeAt returns BadRequestData 400.
    ${r_before}=    Get From SUT    /temporal/entities
    ...    type=AirQualityObserved
    ...    timerel=before
    ...    timeAt=${T2}
    Should Be Equal As Integers    ${r_before.status_code}    200
    ${before_entities}=    Set Variable    ${r_before.json()}
    Should Not Be Empty    ${before_entities}
    FOR    ${entity}    IN    @{before_entities}
        ${instances}=    Set Variable    ${entity}[temperature]
        FOR    ${inst}    IN    @{instances}
            Should Be Equal As Strings    ${inst}[observedAt]    ${T1}
        END
    END

    ${r_between}=    Get From SUT    /temporal/entities
    ...    type=AirQualityObserved
    ...    timerel=between
    ...    timeAt=${T1}
    ...    endTimeAt=${T2_PLUS_1S}
    Should Be Equal As Integers    ${r_between.status_code}    200
    ${between_entities}=    Set Variable    ${r_between.json()}
    Should Not Be Empty    ${between_entities}
    FOR    ${entity}    IN    @{between_entities}
        ${instances}=    Set Variable    ${entity}[temperature]
        ${obs_times}=    Create List
        FOR    ${inst}    IN    @{instances}
            Append To List    ${obs_times}    ${inst}[observedAt]
            Should Not Be Equal As Strings    ${inst}[observedAt]    ${T3}
        END
        List Should Contain Value    ${obs_times}    ${T1}
        List Should Contain Value    ${obs_times}    ${T2}
    END

    ${r_invalid}=    Get From SUT    /temporal/entities
    ...    type=AirQualityObserved
    ...    timerel=after
    Should Be NGSI-LD Error    ${r_invalid}    400    BadRequestData

CIM009 6.18.3.1 Temporal Query With timeproperty lastN And attrs
    [Documentation]    timeproperty=modifiedAt is accepted (200); lastN=1 returns exactly one instance
    ...                (the latest); attrs=temperature restricts attributes in response.
    ${r_prop}=    Get From SUT    /temporal/entities
    ...    type=AirQualityObserved
    ...    timeproperty=modifiedAt
    ...    timerel=after
    ...    timeAt=${T1_MINUS_1S}
    Should Be Equal As Integers    ${r_prop.status_code}    200

    ${r_last}=    Get From SUT    /temporal/entities
    ...    type=AirQualityObserved
    ...    lastN=1
    Should Be Equal As Integers    ${r_last.status_code}    200
    ${last_entities}=    Set Variable    ${r_last.json()}
    Should Not Be Empty    ${last_entities}
    FOR    ${entity}    IN    @{last_entities}
        ${instances}=    Set Variable    ${entity}[temperature]
        ${len}=    Get Length    ${instances}
        Should Be Equal As Integers    ${len}    1
        Should Be Equal As Strings    ${instances}[0][observedAt]    ${T3}
    END

    ${r_attrs}=    Get From SUT    /temporal/entities
    ...    type=AirQualityObserved
    ...    attrs=temperature
    Should Be Equal As Integers    ${r_attrs.status_code}    200
    ${attr_entities}=    Set Variable    ${r_attrs.json()}
    Should Not Be Empty    ${attr_entities}
    FOR    ${entity}    IN    @{attr_entities}
        Dictionary Should Contain Key    ${entity}    temperature
        Dictionary Should Not Contain Key    ${entity}    relativeHumidity
        Dictionary Should Not Contain Key    ${entity}    name
        Dictionary Should Not Contain Key    ${entity}    location
    END

CIM009 6.19.3.1 Retrieve Temporal Entity By Id
    [Documentation]    GET /temporal/entities/{id} returns the temporal representation of the entity;
    ...                unknown entity returns ResourceNotFound 404 with a problem body.
    ${response}=    Get From SUT    /temporal/entities/${SETUP_ID_1}
    Should Be Equal As Integers    ${response.status_code}    200
    ${body}=    Set Variable    ${response.json()}
    Should Be Equal As Strings    ${body}[id]    ${SETUP_ID_1}
    Should Be Equal As Strings    ${body}[type]    AirQualityObserved
    Dictionary Should Contain Key    ${body}    temperature
    ${is_list}=    Evaluate    isinstance($body['temperature'], list)
    Should Be True    ${is_list}

    ${fake_id}=    Random Entity Id    AirQualityObserved
    ${r_notfound}=    Get From SUT    /temporal/entities/${fake_id}
    Should Be NGSI-LD Error    ${r_notfound}    404    ResourceNotFound

CIM009 6.18.3.1 Temporal Query Pagination Limit Yields Next Link Or Count
    [Documentation]    Clause 6.3.10 & 6.18.3.1: limit=1 returns 200 and either Link rel="next"
    ...                or NGSILD-Results-Count header when more results exist.
    ${response}=    Get From SUT    /temporal/entities
    ...    type=AirQualityObserved
    ...    limit=1
    Should Be Equal As Integers    ${response.status_code}    200
    ${items}=    Set Variable    ${response.json()}
    ${len}=    Get Length    ${items}
    Should Be Equal As Integers    ${len}    1
    ${has_link}=    Evaluate    'Link' in $response.headers and 'rel="next"' in $response.headers['Link']
    ${has_count}=    Evaluate    'NGSILD-Results-Count' in $response.headers
    Should Be True    ${has_link} or ${has_count}
    IF    ${has_count}
        ${count}=    Convert To Integer    ${response.headers}[NGSILD-Results-Count]
        Should Be True    ${count} >= 2
    END

CIM009 6.18.3.1 Temporal Aggregation Optional Query
    [Documentation]    aggrMethods=avg with aggrPeriodDuration=PT1H is optional in the claim.
    ...                Passes on 200 with avg present or on 501/400 with a problem body. Never fails lane if unsupported.
    [Tags]    optional
    ${response}=    Get From SUT    /temporal/entities
    ...    type=AirQualityObserved
    ...    timerel=after
    ...    timeAt=${T1_MINUS_1S}
    ...    aggrMethods=avg
    ...    aggrPeriodDuration=PT1H
    IF    ${response.status_code} == 200
        ${entities}=    Set Variable    ${response.json()}
        FOR    ${entity}    IN    @{entities}
            ${instances}=    Set Variable    ${entity}[temperature]
            FOR    ${inst}    IN    @{instances}
                Dictionary Should Contain Key    ${inst}    avg
            END
        END
    ELSE IF    ${response.status_code} == 501 or ${response.status_code} == 400
        ${body}=    Set Variable    ${response.json()}
        Dictionary Should Contain Key    ${body}    type
        Dictionary Should Contain Key    ${body}    title
    ELSE
        Fail    Unexpected status code ${response.status_code} for optional aggregation query
    END
