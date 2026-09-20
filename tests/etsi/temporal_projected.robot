*** Settings ***
Documentation     ETSI GS CIM 009 V1.9.1 temporal read through a ModelProjection (EP-30, MP-02, MP-03).
...               Asserts that temporal representation narrows identically to current-value queries.
Resource          resources/ngsi_ld.resource
Suite Setup       Setup Projected Fixture
Suite Teardown    Teardown NGSI-LD Session
Force Tags        etsi    temporal    projected

*** Variables ***
${SETUP_AQ_ID}     ${EMPTY}
${SETUP_DEV_ID}    ${EMPTY}
${T1}              ${EMPTY}
${T2}              ${EMPTY}
${T3}              ${EMPTY}

*** Keywords ***
Setup Projected Fixture
    IF    '${PROJECTED_URL}' == '${EMPTY}'
        Skip    PROJECTED_URL is empty: skipping projected temporal suite
    END
    Open NGSI-LD Session
    Open Custom NGSI-LD Session    projected_sut    ${PROJECTED_URL}

    ${t1}=    Get ISO Timestamp    -1200
    ${t2}=    Get ISO Timestamp    -600
    ${t3}=    Get ISO Timestamp    0
    Set Suite Variable    ${T1}    ${t1}
    Set Suite Variable    ${T2}    ${t2}
    Set Suite Variable    ${T3}    ${t3}

    ${aq_id}=    Random Entity Id    AirQualityObserved
    ${dev_id}=    Random Entity Id    Device
    Set Suite Variable    ${SETUP_AQ_ID}    ${aq_id}
    Set Suite Variable    ${SETUP_DEV_ID}    ${dev_id}

    ${aq}=    Build AirQuality Entity    ${aq_id}    temp=15.0    humidity=55.0    name=Sonda 1
    ${dev}=    Build Device Entity    ${dev_id}    name=Merač 1    battery=88.0
    ${r_aq}=    Create Entity In SUT    ${aq}
    Should Be Equal As Integers    ${r_aq.status_code}    201
    ${r_dev}=    Create Entity In SUT    ${dev}
    Should Be Equal As Integers    ${r_dev.status_code}    201

    ${obs_aq1}=    Build Temporal Observation Payload    16.0    56.0    ${t1}
    ${obs_aq2}=    Build Temporal Observation Payload    17.0    57.0    ${t2}
    ${obs_aq3}=    Build Temporal Observation Payload    18.0    58.0    ${t3}
    ${ap1}=    Append Entity Attributes    ${aq_id}    ${obs_aq1}
    Should Contain    ${{ [200, 204] }}    ${ap1.status_code}
    ${ap2}=    Append Entity Attributes    ${aq_id}    ${obs_aq2}
    Should Contain    ${{ [200, 204] }}    ${ap2.status_code}
    ${ap3}=    Append Entity Attributes    ${aq_id}    ${obs_aq3}
    Should Contain    ${{ [200, 204] }}    ${ap3.status_code}

    ${obs_d1}=    Build Device Observation Payload    87.0    ${t1}
    ${obs_d2}=    Build Device Observation Payload    86.0    ${t2}
    ${obs_d3}=    Build Device Observation Payload    85.0    ${t3}
    ${apd1}=    Append Entity Attributes    ${dev_id}    ${obs_d1}
    Should Contain    ${{ [200, 204] }}    ${apd1.status_code}
    ${apd2}=    Append Entity Attributes    ${dev_id}    ${obs_d2}
    Should Contain    ${{ [200, 204] }}    ${apd2.status_code}
    ${apd3}=    Append Entity Attributes    ${dev_id}    ${obs_d3}
    Should Contain    ${{ [200, 204] }}    ${apd3.status_code}

*** Test Cases ***
MP02 Temporal Query Exposes Only Projected Attributes
    [Documentation]    MP-02 & MP-03: On projected endpoint, only projected classes and slots
    ...                (temperature) plus id/type are exposed.
    ${response}=    Get From Custom Session    projected_sut    /temporal/entities
    ...    type=AirQualityObserved
    Should Be Equal As Integers    ${response.status_code}    200
    ${entities}=    Set Variable    ${response.json()}
    Should Not Be Empty    ${entities}
    FOR    ${entity}    IN    @{entities}
        Dictionary Should Contain Key    ${entity}    id
        Dictionary Should Contain Key    ${entity}    type
        Dictionary Should Contain Key    ${entity}    temperature
        Dictionary Should Not Contain Key    ${entity}    relativeHumidity
        Dictionary Should Not Contain Key    ${entity}    name
        Dictionary Should Not Contain Key    ${entity}    location
        ${instances}=    Set Variable    ${entity}[temperature]
        FOR    ${inst}    IN    @{instances}
            Dictionary Should Contain Key    ${inst}    value
            Dictionary Should Contain Key    ${inst}    observedAt
            Dictionary Should Not Contain Key    ${inst}    relativeHumidity
        END
    END

MP02 Unprojected Class Device Query Returns Empty Array
    [Documentation]    MP-01 & MP-02: Device is omitted from projection classes, so query returns empty list.
    ${response}=    Get From Custom Session    projected_sut    /temporal/entities
    ...    type=Device
    Should Be Equal As Integers    ${response.status_code}    200
    ${items}=    Set Variable    ${response.json()}
    ${len}=    Get Length    ${items}
    Should Be Equal As Integers    ${len}    0

MP02 Unprojected Class Device Retrieval Returns 404
    [Documentation]    MP-01 & MP-02: Direct retrieval of an unprojected entity returns 404 ResourceNotFound.
    ${response}=    Get From Custom Session    projected_sut    /temporal/entities/${SETUP_DEV_ID}
    Should Be NGSI-LD Error    ${response}    404    ResourceNotFound

EP61 attrs Parameter For Hidden Attribute Returns No Hidden Slots
    [Documentation]    EP-61 & MP-02: Requesting unprojected slot relativeHumidity returns entities without it.
    ${response}=    Get From Custom Session    projected_sut    /temporal/entities
    ...    type=AirQualityObserved
    ...    attrs=relativeHumidity
    Should Be Equal As Integers    ${response.status_code}    200
    ${entities}=    Set Variable    ${response.json()}
    FOR    ${entity}    IN    @{entities}
        Dictionary Should Not Contain Key    ${entity}    relativeHumidity
    END

GW10 Caller Filter q Narrows Projected Entities
    [Documentation]    GW10 & MP-02: Caller q=temperature>0 still narrows, returning a valid subset.
    ${response}=    Get From Custom Session    projected_sut    /temporal/entities
    ...    type=AirQualityObserved
    ...    q=temperature>0
    Should Be Equal As Integers    ${response.status_code}    200
    ${entities}=    Set Variable    ${response.json()}
    Should Not Be Empty    ${entities}

    ${r_empty}=    Get From Custom Session    projected_sut    /temporal/entities
    ...    type=AirQualityObserved
    ...    q=temperature>999
    Should Be Equal As Integers    ${r_empty.status_code}    200
    ${empty_items}=    Set Variable    ${r_empty.json()}
    ${empty_len}=    Get Length    ${empty_items}
    Should Be Equal As Integers    ${empty_len}    0

EP30 Current Value And Temporal Representation Parity
    [Documentation]    EP-07 & EP-30: Narrowing holds in history exactly as in current values.
    ...                Current value GET /entities exposes the exact same projected attribute set.
    ${r_curr}=    Get From Custom Session    projected_sut    /entities
    ...    type=AirQualityObserved
    Should Be Equal As Integers    ${r_curr.status_code}    200
    ${curr_entities}=    Set Variable    ${r_curr.json()}
    Should Not Be Empty    ${curr_entities}
    ${curr_keys}=    Evaluate    sorted([k for k in $curr_entities[0].keys() if not k.startswith('@')])

    ${r_temp}=    Get From Custom Session    projected_sut    /temporal/entities
    ...    type=AirQualityObserved
    Should Be Equal As Integers    ${r_temp.status_code}    200
    ${temp_entities}=    Set Variable    ${r_temp.json()}
    Should Not Be Empty    ${temp_entities}
    ${temp_keys}=    Evaluate    sorted([k for k in $temp_entities[0].keys() if not k.startswith('@')])

    Should Be Equal    ${curr_keys}    ${temp_keys}
    List Should Contain Value    ${curr_keys}    id
    List Should Contain Value    ${curr_keys}    type
    List Should Contain Value    ${curr_keys}    temperature
    List Should Not Contain Value    ${curr_keys}    relativeHumidity
